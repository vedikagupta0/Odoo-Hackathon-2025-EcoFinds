from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity, verify_jwt_in_request
from models.user import User
from models.product import Product
from models.cart_item import CartItem
from models.order import Order
from models.order_item import OrderItem
from extensions import db
from werkzeug.exceptions import BadRequest, NotFound, Unauthorized, UnprocessableEntity

cart_bp = Blueprint('cart', __name__)

@cart_bp.route('', methods=['GET'])
@jwt_required()
def get_cart():
    try:
        current_user_id = get_jwt_identity()
        user = User.query.get(current_user_id)
        
        if not user:
            return jsonify({"message": "User not found"}), 404
        
        cart_items = CartItem.query.filter_by(user_id=current_user_id).all()
        
        # Safely calculate total and check for missing/sold products
        valid_items = []
        unavailable_items = []
        total = 0
        
        for item in cart_items:
            if item.product:
                if not item.product.is_sold:
                    total += item.product.price
                    valid_items.append(item.to_dict())
                else:
                    unavailable_items.append({
                        "id": item.id,
                        "product_id": item.product_id,
                        "reason": "sold"
                    })
            else:
                unavailable_items.append({
                    "id": item.id,
                    "product_id": item.product_id if hasattr(item, 'product_id') else None,
                    "reason": "missing"
                })
        
        return jsonify({
            "cartItems": valid_items,
            "total": total,
            "unavailableItems": unavailable_items
        }), 200
    except Exception as e:
        current_app.logger.error(f"Error fetching cart: {str(e)}")
        return jsonify({
            "message": "Error fetching cart items",
            "details": str(e)
        }), 500

@cart_bp.route('', methods=['POST'])
@jwt_required()
def add_to_cart():
    current_user_id = get_jwt_identity()
    user = User.query.get(current_user_id)
    
    if not user:
        return jsonify({"message": "User not found"}), 404
    
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"message": "No JSON data provided", "details": "Request body is empty"}), 400
        
        if 'product_id' not in data:
            return jsonify({"message": "Product ID is required", "details": "Missing product_id field in request"}), 400
        
        try:
            product_id = int(data['product_id'])
        except (ValueError, TypeError):
            return jsonify({"message": "Invalid product ID", "details": "Product ID must be a number"}), 400
        
        product = Product.query.get(product_id)
        
        if not product:
            return jsonify({"message": "Product not found", "details": f"No product found with ID {product_id}"}), 404
        
        if product.is_sold:
            return jsonify({"message": "Product is no longer available", "details": "This item has already been sold"}), 400
        
        # Check if product is already in cart
        existing_item = CartItem.query.filter_by(user_id=current_user_id, product_id=product_id).first()
        if existing_item:
            return jsonify({
                "message": "Product is already in your cart", 
                "details": "This item is already in your shopping cart",
                "cartItem": existing_item.to_dict()
            }), 400
        
        # Prevent adding own products to cart
        if product.seller_id == current_user_id:
            return jsonify({"message": "You cannot add your own products to cart", "details": "You are the seller of this item"}), 400
        
        # Add to cart
        cart_item = CartItem(user_id=current_user_id, product_id=product_id)
        db.session.add(cart_item)
        db.session.commit()
        
        return jsonify({
            "message": "Product added to cart successfully",
            "cartItem": cart_item.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        print(f"Error adding product to cart: {str(e)}")
        return jsonify({
            "message": "Error processing your request",
            "details": str(e)
        }), 500

@cart_bp.route('/<int:id>', methods=['DELETE'])
@jwt_required()
def remove_from_cart(id):
    current_user_id = get_jwt_identity()
    
    cart_item = CartItem.query.get(id)
    
    if not cart_item:
        return jsonify({"message": "Cart item not found"}), 404
    
    if cart_item.user_id != current_user_id:
        return jsonify({"message": "You don't have permission to remove this item"}), 403
    
    db.session.delete(cart_item)
    db.session.commit()
    
    return jsonify({
        "message": "Item removed from cart"
    }), 200

@cart_bp.route('/checkout', methods=['POST'])
@jwt_required()
def checkout():
    try:
        current_user_id = get_jwt_identity()
        user = User.query.get(current_user_id)
        
        if not user:
            return jsonify({"message": "User not found", "details": "The authenticated user could not be found"}), 404
        
        # Parse input data
        try:
            data = request.get_json() or {}
        except BadRequest:
            return jsonify({
                "message": "Invalid JSON data", 
                "details": "The provided data is not valid JSON"
            }), 400
            
        shipping_address = data.get('shipping_address', '')
        
        # Get cart items
        cart_items = CartItem.query.filter_by(user_id=current_user_id).all()
        
        if not cart_items:
            return jsonify({
                "message": "Your cart is empty",
                "details": "Add items to your cart before checkout"
            }), 400
        
        # Check if all products are available
        unavailable_products = []
        for item in cart_items:
            if not item.product or item.product.is_sold:
                unavailable_products.append({
                    "id": item.product_id if item.product else item.id,
                    "name": item.product.title if item.product else "Unknown product"
                })
        
        if unavailable_products:
            return jsonify({
                "message": "Some products are no longer available",
                "details": "The following items are no longer available for purchase",
                "unavailableProducts": unavailable_products
            }), 400
        
        # Calculate total
        total_amount = sum(item.product.price for item in cart_items)
        
        # Create order
        order = Order(
            user_id=current_user_id,
            total_amount=total_amount,
            shipping_address=shipping_address
        )
        db.session.add(order)
        
        # Create order items and mark products as sold
        for cart_item in cart_items:
            # Create order item
            order_item = OrderItem(
                order=order,
                product_id=cart_item.product_id,
                price=cart_item.product.price
            )
            db.session.add(order_item)
            
            # Mark product as sold
            cart_item.product.is_sold = True
            
            # Remove from cart
            db.session.delete(cart_item)
        
        db.session.commit()
        
        return jsonify({
            "message": "Order placed successfully",
            "order": order.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Checkout error: {str(e)}")
        return jsonify({
            "message": "Error processing your order",
            "details": str(e)
        }), 500