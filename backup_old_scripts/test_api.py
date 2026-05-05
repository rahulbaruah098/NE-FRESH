import requests
import json
import time

# For local testing: 
BASE_URL = "http://localhost:5000/api"

class APITester:
    def __init__(self):
        self.token = None
        self.user_id = None
        
    def test_register(self):
        """Test user registration"""
        print("\n=== Testing Registration ===")
        data = {
            "name": "Test User",
            "email": f"testuser_{int(time.time())}@test.com",
            "phone": "9876543210",
            "password": "test123456"
        }
        
        response = requests.post(f"{BASE_URL}/auth/register", json=data)
        print(f"Status: {response.status_code}")
        result = response.json()
        print(f"Response: {json.dumps(result, indent=2)}")
        
        if result.get('success'):
            self.token = result.get('token')
            self.user_id = result['user']['id']
            print(f"✓ Registration successful! Token: {self.token[:20]}...")
            return True
        else:
            print(f"✗ Registration failed: {result.get('error')}")
            return False
    
    def test_login(self, email, password):
        """Test user login"""
        print("\n=== Testing Login ===")
        data = {
            "email": email,
            "password": password
        }
        
        response = requests.post(f"{BASE_URL}/auth/login", json=data)
        print(f"Status: {response.status_code}")
        result = response.json()
        print(f"Response: {json.dumps(result, indent=2)}")
        
        if result.get('success'):
            self.token = result.get('token')
            self.user_id = result['user']['id']
            print(f"✓ Login successful!")
            return True
        else:
            print(f"✗ Login failed: {result.get('error')}")
            return False
    
    def test_products_list(self):
        """Test fetching products"""
        print("\n=== Testing Products List ===")
        response = requests.get(f"{BASE_URL}/products")
        print(f"Status: {response.status_code}")
        result = response.json()
        
        if result.get('success'):
            products = result.get('products', [])
            print(f"✓ Found {len(products)} products")
            if products:
                print(f"First product: {products[0].get('name')}")
            return True
        else:
            print(f"✗ Failed to fetch products")
            return False
    
    def test_product_detail(self, product_id=1):
        """Test fetching single product"""
        print(f"\n=== Testing Product Detail (ID: {product_id}) ===")
        response = requests.get(f"{BASE_URL}/products/{product_id}")
        print(f"Status: {response.status_code}")
        result = response.json()
        print(f"Response: {json.dumps(result, indent=2)}")
        
        if result.get('success'):
            print(f"✓ Product details fetched successfully")
            return True
        else:
            print(f"✗ Failed to fetch product details")
            return False
    
    def test_profile(self):
        """Test fetching user profile"""
        print("\n=== Testing User Profile ===")
        headers = {"Authorization": f"Bearer {self.token}"}
        response = requests.get(f"{BASE_URL}/user/profile", headers=headers)
        print(f"Status: {response.status_code}")
        result = response.json()
        print(f"Response: {json.dumps(result, indent=2)}")
        
        if result.get('success'):
            print(f"✓ Profile fetched successfully")
            return True
        else:
            print(f"✗ Failed to fetch profile")
            return False
    
    def test_cart_operations(self):
        """Test cart operations"""
        print("\n=== Testing Cart Operations ===")
        headers = {"Authorization": f"Bearer {self.token}"}
        
        # Add to cart
        print("\n--- Adding to cart ---")
        data = {"product_id": 1, "quantity": 2.5}
        response = requests.post(f"{BASE_URL}/cart", json=data, headers=headers)
        print(f"Status: {response.status_code}")
        result = response.json()
        print(f"Add result: {json.dumps(result, indent=2)}")
        
        # Get cart
        print("\n--- Getting cart ---")
        response = requests.get(f"{BASE_URL}/cart", headers=headers)
        print(f"Status: {response.status_code}")
        result = response.json()
        print(f"Cart: {json.dumps(result, indent=2)}")
        
        if result.get('success') and result.get('items'):
            cart_item_id = result['items'][0]['id']
            
            # Remove from cart
            print(f"\n--- Removing from cart (ID: {cart_item_id}) ---")
            response = requests.delete(f"{BASE_URL}/cart/{cart_item_id}", headers=headers)
            print(f"Status: {response.status_code}")
            result = response.json()
            print(f"Remove result: {json.dumps(result, indent=2)}")
            
            return result.get('success', False)
        
        return False
    
    def test_orders_list(self):
        """Test fetching orders"""
        print("\n=== Testing Orders List ===")
        headers = {"Authorization": f"Bearer {self.token}"}
        response = requests.get(f"{BASE_URL}/orders", headers=headers)
        print(f"Status: {response.status_code}")
        result = response.json()
        print(f"Response: {json.dumps(result, indent=2)}")
        
        if result.get('success'):
            orders = result.get('orders', [])
            print(f"✓ Found {len(orders)} orders")
            return True
        else:
            print(f"✗ Failed to fetch orders")
            return False
    
    def test_logout(self):
        """Test logout"""
        print("\n=== Testing Logout ===")
        headers = {"Authorization": f"Bearer {self.token}"}
        response = requests.post(f"{BASE_URL}/auth/logout", headers=headers)
        print(f"Status: {response.status_code}")
        result = response.json()
        print(f"Response: {json.dumps(result, indent=2)}")
        
        if result.get('success'):
            print(f"✓ Logout successful")
            self.token = None
            return True
        else:
            print(f"✗ Logout failed")
            return False

def main():
    tester = APITester()
    
    print("=" * 60)
    print("API Testing for Chhimphei Chicken")
    print("=" * 60)
    
    # Run tests
    tests = [
        ("Registration", lambda: tester.test_register()),
        ("Products List", lambda: tester.test_products_list()),
        ("Product Detail", lambda: tester.test_product_detail(1)),
        ("User Profile", lambda: tester.test_profile()),
        ("Cart Operations", lambda: tester.test_cart_operations()),
        ("Orders List", lambda: tester.test_orders_list()),
        ("Logout", lambda: tester.test_logout()),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            success = test_func()
            results.append((name, success))
        except Exception as e:
            print(f"\n✗ {name} failed with error: {e}")
            results.append((name, False))
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    for name, success in results:
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"{status}: {name}")
    
    passed = sum(1 for _, success in results if success)
    total = len(results)
    print(f"\nTotal: {passed}/{total} tests passed")

if __name__ == "__main__":
    main()