#!/usr/bin/env python3
"""
Setup Gemini API Key for E-Derma
This script helps you configure your Gemini API key properly.
"""

import os
import re

def setup_gemini_key():
    """Interactive setup for Gemini API key."""
    print("🔑 E-Derma Gemini API Key Setup")
    print("=" * 50)
    
    print("\n📋 Steps to get your API key:")
    print("1. Go to: https://aistudio.google.com/")
    print("2. Sign in with your Google account")
    print("3. Click 'Get API Key' in the top right")
    print("4. Create a new API key")
    print("5. Copy the API key (starts with 'AIzaSy')")
    
    print("\n" + "-" * 50)
    
    # Get API key from user
    while True:
        api_key = input("🔑 Enter your Gemini API key: ").strip()
        
        if not api_key:
            print("❌ Please enter an API key")
            continue
        
        if not api_key.startswith("AIzaSy"):
            print("❌ Invalid API key format. Should start with 'AIzaSy'")
            continue
        
        if len(api_key) < 30:
            print("❌ API key seems too short. Please check and try again.")
            continue
        
        break
    
    # Update files
    files_to_update = [
        "app.py",
        "test_gemini.py", 
        "debug_gemini.py"
    ]
    
    print(f"\n🔄 Updating {len(files_to_update)} files...")
    
    for filename in files_to_update:
        if os.path.exists(filename):
            try:
                # Read file
                with open(filename, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Replace API key
                # Pattern to match: GEMINI_API_KEY = "anything"
                pattern = r'GEMINI_API_KEY\s*=\s*["\'][^"\']*["\']'
                replacement = f'GEMINI_API_KEY = "{api_key}"'
                
                new_content = re.sub(pattern, replacement, content)
                
                # Write back
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                
                print(f"✅ Updated {filename}")
                
            except Exception as e:
                print(f"❌ Failed to update {filename}: {e}")
        else:
            print(f"⚠️ File not found: {filename}")
    
    print("\n🧪 Testing API key...")
    
    # Test the API key
    import requests
    import json
    
    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }
    
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": "Hello! Please respond with 'API Working' if you receive this."}
                ]
            }
        ]
    }
    
    endpoint = "https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent"
    
    try:
        print("📡 Making test API call...")
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=10)
        
        if resp.status_code == 200:
            print("✅ SUCCESS! Gemini API is working!")
            print("🎉 Your E-Derma app will now use Gemini AI for skin analysis")
            return True
        else:
            print(f"❌ API Error: {resp.status_code}")
            print(f"Response: {resp.text}")
            return False
            
    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

if __name__ == "__main__":
    success = setup_gemini_key()
    
    if success:
        print("\n" + "=" * 50)
        print("🎯 SETUP COMPLETE!")
        print("✅ Gemini API key configured successfully")
        print("✅ All files updated")
        print("✅ API test passed")
        print("\n💡 Next steps:")
        print("1. Restart your Flask app: python app.py")
        print("2. Try a skin analysis - it should now use Gemini AI!")
        print("3. Check admin panel to see Gemini usage statistics")
    else:
        print("\n" + "=" * 50)
        print("❌ SETUP FAILED")
        print("🔧 Please check:")
        print("1. Your API key is correct")
        print("2. You have internet connection")
        print("3. Gemini API is not blocked in your region")
        print("\n💡 You can run this script again: python setup_gemini_key.py")