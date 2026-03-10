"""
Verify Auto Coach API.
"""
import requests
import time

def test_api():
    print("Testing Auto Coach API...")
    url = "http://localhost:8000/api/coach/report"
    
    try:
        response = requests.get(url)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json()}")
        
        if response.status_code == 200:
            print("PASS: API endpoint is reachable.")
        else:
            print("FAIL: API returned error.")
            
    except Exception as e:
        print(f"FAIL: Could not connect to API. Error: {e}")
        print("Note: Ensure the backend is running.")

if __name__ == "__main__":
    test_api()
