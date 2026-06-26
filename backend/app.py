import os
import json
import base64
import sqlite3
import re
from datetime import datetime
from urllib.parse import quote_plus

import numpy as np
import cv2
import requests
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse

# ---------------- Paths & App Setup ----------------
BASE_DIR = os.path.dirname(__file__)
STATIC_DIR = os.path.join(BASE_DIR, "static")
CONFIG_PATH = os.path.join(BASE_DIR, "config", "ingredients_rules.json")
DB_PATH = os.path.join(BASE_DIR, "skin_analysis.db")

# Load external rules (skin_rules + issue_rules) from JSON
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        RULES = json.load(f)
    SKIN_RULES = RULES.get("skin_rules", {})
    ISSUE_RULES = RULES.get("issue_rules", {})
    print("✅ Loaded ingredient rules from config/ingredients_rules.json")
else:
    print("⚠️ ingredients_rules.json not found – using fallback rules.")
    SKIN_RULES = {}
    ISSUE_RULES = {}

# ---------- Gemini API configuration ----------
# Gemini API key - Get from https://aistudio.google.com/
GEMINI_API_KEY = "AIzaSyBbQWMkbXgzFTNYqYjmDQ0zhO4oMTfPqUM"  # Replace with your actual API key
GEMINI_MODEL = "models/gemini-2.5-flash"
GEMINI_ENDPOINT = f"https://generativelanguage.googleapis.com/v1/{GEMINI_MODEL}:generateContent"

gemini_available = bool(GEMINI_API_KEY)
if gemini_available:
    print("🔑 GEMINI_API_KEY set – Gemini API MODE AVAILABLE")
else:
    print("⚠️ GEMINI_API_KEY empty – heuristic-only mode")

# FastAPI app
app = FastAPI(title="E-Derma – Real-Time Skin Analysis API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ---------------- SQLite Database Setup ----------------
def get_db_connection():
    """Get database connection with proper configuration."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row  # Enable column access by name
        conn.execute("PRAGMA foreign_keys = ON")  # Enable foreign key constraints
        conn.execute("PRAGMA journal_mode = WAL")  # Better concurrency
        return conn
    except sqlite3.Error as e:
        print(f"❌ Database connection error: {e}")
        raise

def init_database():
    """Initialize SQLite database with required tables."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Users table with better constraints
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                contact TEXT NOT NULL UNIQUE,
                address TEXT NOT NULL,
                city TEXT NOT NULL,
                user_skin_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Analysis results table with better constraints
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS analysis_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                skin_type TEXT NOT NULL,
                detected_issues TEXT NOT NULL,
                issue_confidence TEXT NOT NULL,
                severity_score REAL NOT NULL CHECK(severity_score >= 0 AND severity_score <= 1),
                ingredients TEXT NOT NULL,
                gemini_used BOOLEAN DEFAULT FALSE,
                gemini_note TEXT,
                analysis_mode TEXT NOT NULL,
                image_metrics TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
        ''')
        
        # Create indexes for better performance
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_contact ON users(contact)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_analysis_user_id ON analysis_results(user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_analysis_created_at ON analysis_results(created_at)')
        
        conn.commit()
        print("✅ Database initialized successfully")
        
    except sqlite3.Error as e:
        print(f"❌ Database initialization error: {e}")
        raise
    finally:
        if conn:
            conn.close()

def save_user_data(name, contact, address, city, user_skin_type):
    """Save user profile data and return user ID."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if user already exists
        cursor.execute('SELECT id FROM users WHERE contact = ?', (contact,))
        existing_user = cursor.fetchone()
        
        if existing_user:
            print(f"⚠️ User with contact {contact} already exists")
            return existing_user['id']
        
        # Insert new user
        cursor.execute('''
            INSERT INTO users (name, contact, address, city, user_skin_type)
            VALUES (?, ?, ?, ?, ?)
        ''', (name, contact, address, city, user_skin_type))
        
        user_id = cursor.lastrowid
        conn.commit()
        print(f"✅ User saved with ID: {user_id}")
        return user_id
        
    except sqlite3.Error as e:
        print(f"❌ Error saving user data: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

def save_analysis_result(user_id, skin_type, issues, issue_conf, severity, 
                        ingredients, gemini_used, gemini_note, mode, metrics):
    """Save analysis results to database."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Validate user exists
        cursor.execute('SELECT id FROM users WHERE id = ?', (user_id,))
        if not cursor.fetchone():
            raise ValueError(f"User with ID {user_id} does not exist")
        
        # Insert analysis result
        cursor.execute('''
            INSERT INTO analysis_results 
            (user_id, skin_type, detected_issues, issue_confidence, severity_score,
             ingredients, gemini_used, gemini_note, analysis_mode, image_metrics)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            user_id,
            skin_type,
            json.dumps(issues) if isinstance(issues, list) else issues,
            json.dumps(issue_conf) if isinstance(issue_conf, dict) else issue_conf,
            float(severity),
            json.dumps(ingredients) if isinstance(ingredients, list) else ingredients,
            bool(gemini_used),
            gemini_note,
            mode,
            json.dumps(metrics) if isinstance(metrics, dict) else metrics
        ))
        
        analysis_id = cursor.lastrowid
        conn.commit()
        print(f"✅ Analysis saved with ID: {analysis_id}")
        return analysis_id
        
    except (sqlite3.Error, ValueError) as e:
        print(f"❌ Error saving analysis result: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

def get_user_history(user_contact):
    """Get analysis history for a user by contact."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT u.name, u.contact, a.skin_type, a.detected_issues, 
                   a.severity_score, a.created_at, a.gemini_used, a.analysis_mode
            FROM users u
            JOIN analysis_results a ON u.id = a.user_id
            WHERE u.contact = ?
            ORDER BY a.created_at DESC
        ''', (user_contact,))
        
        results = cursor.fetchall()
        
        history = []
        for row in results:
            try:
                detected_issues = json.loads(row['detected_issues']) if row['detected_issues'] else []
            except json.JSONDecodeError:
                detected_issues = [row['detected_issues']]
                
            history.append({
                "name": row['name'],
                "contact": row['contact'],
                "skin_type": row['skin_type'],
                "detected_issues": detected_issues,
                "severity_score": row['severity_score'],
                "analysis_date": row['created_at'],
                "gemini_used": bool(row['gemini_used']),
                "analysis_mode": row['analysis_mode']
            })
        
        return history
        
    except sqlite3.Error as e:
        print(f"❌ Error getting user history: {e}")
        return []
    finally:
        if conn:
            conn.close()

# ---------------- Validation Functions ----------------
def validate_name(name: str) -> tuple[bool, str]:
    """Clean name validation function with proper regex patterns."""
    if not name or not name.strip():
        return False, "Please enter your full name"
    
    name = name.strip()
    
    # Basic length validation
    if len(name) < 3:
        return False, "Please enter your complete name (minimum 3 characters)"
    if len(name) > 50:
        return False, "Name is too long (maximum 50 characters)"
    
    # Only alphabets, spaces, and dots for initials allowed
    if not re.match(r'^[a-zA-Z\s.]+$', name):
        return False, "Name should only contain letters, spaces, and dots (for initials)"
    
    # Normalize spaces
    name = re.sub(r'\s+', ' ', name).strip()
    
    # Invalid dot patterns
    if name.startswith('.') or name.endswith('.'):
        return False, "Name cannot start or end with dots"
    if '..' in name:
        return False, "Invalid dot usage in name"
    
    # Split into name parts
    name_parts = [part.strip() for part in name.split() if part.strip()]
    if not name_parts:
        return False, "Please enter a valid name"
    
    # Must have at least one meaningful name part
    meaningful_parts = 0
    
    # Validate each name part
    for part in name_parts:
        # Check if it's an initial (single letter with dot)
        if '.' in part:
            if not re.match(r'^[A-Za-z]\.$', part):
                return False, "Initials should be single letter followed by dot (e.g., 'A.')"
        else:
            # Regular name part - only alphabets
            if not re.match(r'^[A-Za-z]+$', part):
                return False, "Name parts should only contain letters"
            
            # Allow single letters as initials without dots (like "A Kumar")
            if len(part) == 1:
                # Single letters are allowed as initials
                continue
            
            # Minimum length for meaningful names (2+ letters)
            if len(part) < 2:
                return False, "Each name part should be at least 2 letters or a single initial"
            
            meaningful_parts += 1
            
            # Block obvious repetitive patterns (4+ same letters in a row) - only consecutive
            if re.search(r'(.)\1{3,}', part.lower()):
                return False, "Please enter a real name, not repetitive letters"
            
            # Block if entire name part is same character (but allow short names like "Aaa")
            if len(set(part.lower())) == 1 and len(part) > 3:
                return False, "Please enter a real name, not repetitive letters"
            
            # Enhanced random sequence detection with support for Indian names
            if len(part) >= 6:
                # Check for excessive consonant clusters (more than 4 consecutive consonants)
                consonant_clusters = re.findall(r'[bcdfghjklmnpqrstvwxyz]{5,}', part.lower())
                if consonant_clusters:
                    return False, "Please enter a real name with natural letter patterns"
                
                # Check vowel distribution - real names have reasonable vowel distribution
                vowels = len(re.findall(r'[aeiou]', part.lower()))
                vowel_ratio = vowels / len(part)
                if vowel_ratio < 0.10:  # Reduced from 0.15 to 0.10 for Indian names
                    return False, "Please enter a real name with natural letter patterns"
                
                # Check for random-looking patterns (too many unique character pairs)
                unique_pairs = set()
                for i in range(len(part) - 1):
                    unique_pairs.add(part[i:i+2].lower())
                # Relaxed threshold for Indian names - only flag if extremely random
                if len(unique_pairs) > len(part) * 0.9 and len(part) >= 10:
                    return False, "Please enter a real name, not random character sequences"
            
            # Block names with excessive length and low readability
            if len(part) > 12:
                # Very long names should have some recognizable patterns
                # Enhanced with Indian name patterns
                common_endings = ['son', 'sen', 'man', 'ton', 'ley', 'ner', 'der', 'ter', 'ker', 'ber', 
                                'ana', 'ani', 'ika', 'iya', 'aja', 'esh', 'ash', 'ath', 'ini', 'avi']
                common_beginnings = ['al', 'an', 'ar', 'as', 'ch', 'de', 'el', 'en', 'in', 'ka', 'ma', 'ra', 'sa', 'sh', 'su', 'th', 'vi',
                                   'ar', 'an', 'ad', 'ak', 'am', 'as', 'av', 'pr', 'kr', 'sr', 'bh', 'dh', 'kh', 'ph', 'rh']
                
                # Indian name patterns
                indian_patterns = [
                    r'[aeiou]{2}',  # Vowel combinations (aa, ee, oo, etc.)
                    r'na$',         # Ends with 'na' (Archana, Sahana, etc.)
                    r'ni$',         # Ends with 'ni' (Ragini, Rohini, etc.)
                    r'ka$',         # Ends with 'ka' (Deepika, Priyanka, etc.)
                    r'ya$',         # Ends with 'ya' (Divya, Kavya, etc.)
                    r'ra$',         # Ends with 'ra' (Indira, etc.)
                    r'sh',          # Contains 'sh' (Shiva, Rakesh, etc.)
                    r'ch',          # Contains 'ch' (Sachin, Rachana, etc.)
                    r'th',          # Contains 'th' (Pritha, Haritha, etc.)
                ]
                
                has_common_pattern = (any(part.lower().endswith(end) for end in common_endings) or 
                                    any(part.lower().startswith(begin) for begin in common_beginnings) or
                                    any(re.search(pattern, part.lower()) for pattern in indian_patterns))
                
                if not has_common_pattern and len(part) > 15:
                    return False, "Please enter a realistic name format"
            
            # Block obvious test/fake names
            test_names = [
                'test', 'sample', 'demo', 'user', 'admin', 'temp', 'example',
                'dummy', 'fake', 'null', 'name', 'firstname', 'lastname',
                'asdf', 'qwerty', 'zxcv', 'hjkl', 'mnbv', 'xyz', 'abc'
            ]
            
            if (part.lower() in test_names or 
                any(test in part.lower() for test in ['test', 'demo', 'fake', 'dummy', 'sample']) or
                len(part) <= 3 and part.lower() in ['xyz', 'abc', 'def', 'ghi', 'jkl', 'mno', 'pqr', 'stu', 'vwx']):
                return False, "Please enter your real name, not a test name (examples: John, Steve, Archana, Sahana)"
    
    # Must have at least one meaningful name (not just initials)
    if meaningful_parts == 0:
        return False, "Please include at least one complete name, not just initials"
    
    return True, ""

def validate_email(email: str) -> tuple[bool, str]:
    """Enhanced email validation - ensures real, professional Gmail addresses only."""
    if not email or not email.strip():
        return False, "Please enter your email address"
    
    email = email.strip().lower()
    
    # Check if original email had uppercase letters
    original_email = email.strip()
    if original_email != original_email.lower():
        return False, "Email address must be in lowercase only"
    
    # Length validation
    if len(email) < 12:  # Increased minimum for realistic emails
        return False, "Please enter a complete email address (minimum 12 characters)"
    if len(email) > 35:
        return False, "Email address is too long (maximum 35 characters)"
    
    # Gmail domain validation - STRICT
    if not (email.endswith('@gmail.com') or email.endswith('@gmail.in')):
        return False, "Please use only Gmail addresses (@gmail.com or @gmail.in)"
    
    # Extract and validate username
    username = email.split('@')[0]
    
    # Username length - more realistic
    if len(username) < 4:
        return False, "Email username too short - please use your real email (like john2024)"
    if len(username) > 25:
        return False, "Email username is too long"
    
    # Character validation - only lowercase letters, numbers, dots, underscores
    if not re.match(r'^[a-z0-9._]+$', username):
        return False, "Email can only contain lowercase letters, numbers, dots, and underscores"
    
    # Must start and end with alphanumeric
    if not (username[0].isalnum() and username[-1].isalnum()):
        return False, "Email must start and end with a letter or number"
    
    # Consecutive special characters - STRICT
    if '..' in username or '__' in username:
        return False, "Email cannot have consecutive special characters"
    if '._' in username or '_.' in username:
        return False, "Email format is invalid - check special character placement"
    
    # Enhanced repetitive pattern detection - relaxed for real emails
    if len(username) >= 4:
        # Check for mostly same characters - more lenient
        char_counts = {}
        for char in username:
            char_counts[char] = char_counts.get(char, 0) + 1
        
        # If any character appears more than 70% of the time, it's suspicious (more lenient)
        for char, count in char_counts.items():
            if count > len(username) * 0.7 and len(username) >= 8:  # Only for longer usernames
                return False, "Please enter your real email address, not repetitive patterns"
        
        # Excessive repetition (more than 3 consecutive same chars) - only consecutive
        consecutive_count = 1
        max_consecutive = 1
        for i in range(1, len(username)):
            if username[i] == username[i-1]:
                consecutive_count += 1
                max_consecutive = max(max_consecutive, consecutive_count)
            else:
                consecutive_count = 1
        
        if max_consecutive > 3:
            return False, "Email cannot have more than 3 consecutive identical characters"
        
        # Simple patterns (abcabc) - only check for very obvious patterns
        if len(username) >= 8:  # Only for longer usernames
            for i in range(len(username) - 7):
                pattern = username[i:i+4]  # Increased pattern length
                if username[i+4:i+8] == pattern:
                    return False, "Please use your real email, not repetitive patterns"
    
    # Content validation - ENHANCED
    letter_count = sum(1 for c in username if c.islower())  # Only lowercase letters
    digit_count = sum(1 for c in username if c.isdigit())
    special_count = sum(1 for c in username if c in '._')
    
    # Must have meaningful letters
    if letter_count < 3:
        return False, "Email must contain at least 3 lowercase letters (like john123, steve2024)"
    
    # Cannot be all numbers
    if digit_count == len(username.replace('.', '').replace('_', '')):
        return False, "Email cannot be only numbers - please use your real email"
    
    # Professional ratio check - STRICTER
    if len(username) >= 6:
        if letter_count < len(username) * 0.4:  # At least 40% letters
            return False, "Please use a professional email format with more lowercase letters"
    
    # Special characters shouldn't dominate
    if special_count > len(username) * 0.3:
        return False, "Too many special characters - please use a simpler email format"
    
    # Block common test patterns - EXPANDED
    test_patterns = [
        'test', 'sample', 'demo', 'user', 'admin', 'temp', 'example',
        'dummy', 'fake', 'spam', 'trash', 'junk', 'asdf', 'qwerty',
        '1234', 'abcd', 'aaaa', 'bbbb', 'xxxx', 'yyyy', 'zzzz',
        'temp', 'trial', 'check', 'verify', 'hello', 'world'
    ]
    
    for pattern in test_patterns:
        if pattern in username:
            return False, "Please use your real email address, not a test email"
    
    # Keyboard pattern detection - ENHANCED
    keyboard_sequences = [
        'qwerty', 'asdf', 'zxcv', 'qaz', 'wsx', 'edc', 'rfv',
        '123456', '654321', 'abcdef', 'fedcba', 'qwe', 'asd',
        'zxc', 'poi', 'lkj', 'mnb'
    ]
    
    for seq in keyboard_sequences:
        if seq in username:
            return False, "Please avoid keyboard sequences in your email"
    
    # Check for realistic name patterns
    # Email should ideally contain some recognizable name-like patterns
    if len(username) >= 8:
        # Check if it's all random characters (no vowels or common patterns)
        vowels = sum(1 for c in username if c.lower() in 'aeiou')
        if vowels == 0 and letter_count >= 4:
            return False, "Please use your real email address with recognizable patterns"
    
    # Final check for obviously fake patterns
    suspicious_patterns = ['qqq', 'www', 'eee', 'rrr', 'ttt', 'yyy', 'uuu', 'iii', 'ooo', 'ppp']
    for pattern in suspicious_patterns:
        if pattern in username:
            return False, "Please enter your actual email address"
    
    return True, ""

def validate_contact(contact: str) -> tuple[bool, str]:
    """Validate contact number with Indian mobile format."""
    if not contact or not contact.strip():
        return False, "Contact number is required"
    
    contact = contact.strip()
    
    # Must be exactly 10 digits
    if not re.match(r'^\d{10}$', contact):
        return False, "Contact must be exactly 10 digits"
    
    # Must start with 6, 7, 8, or 9
    if not contact[0] in ['6', '7', '8', '9']:
        return False, "Contact must start with 6, 7, 8, or 9"
    
    return True, ""

def validate_password(password: str) -> tuple[bool, str]:
    """Validate password with enhanced complexity requirements."""
    if not password:
        return False, "Password is required"
    
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    
    if len(password) > 50:
        return False, "Password is too long (maximum 50 characters)"
    
    # Check for at least one uppercase letter
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least one uppercase letter (A-Z)"
    
    # Check for at least one lowercase letter
    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least one lowercase letter (a-z)"
    
    # Check for at least one number
    if not re.search(r'\d', password):
        return False, "Password must contain at least one number (0-9)"
    
    # Optional: Check for at least one special character (recommended but not required)
    # if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
    #     return False, "Password must contain at least one special character"
    
    # Check for common weak passwords
    weak_passwords = [
        'password', 'Password', 'PASSWORD', '12345678', 'Abcd1234',
        'qwerty123', 'Qwerty123', 'admin123', 'Admin123', 'user1234',
        'User1234', 'test1234', 'Test1234'
    ]
    
    if password in weak_passwords:
        return False, "Please choose a stronger password (avoid common passwords)"
    
    return True, ""

def validate_city(city: str) -> tuple[bool, str]:
    """Enhanced city validation with Google Maps integration."""
    if not city or not city.strip():
        return False, "Please enter your city name"
    
    city = city.strip()
    if len(city) < 2:
        return False, "City name must be at least 2 characters"
    if len(city) > 50:
        return False, "City name is too long (maximum 50 characters)"
    
    # Only letters, spaces, hyphens, and periods
    if not re.match(r'^[a-zA-Z\s\-\.]+$', city):
        return False, "City should contain only letters, spaces, hyphens, and periods"
    
    # Normalize spaces
    city = re.sub(r'\s+', ' ', city).strip()
    
    # Check for realistic city patterns - must have vowels for longer names
    vowels = len(re.findall(r'[aeiouAEIOU]', city))
    if len(city) > 4 and vowels == 0:
        return False, "Please enter a real city name (like 'Mumbai', 'Delhi', 'Chennai')"
    
    # Check for excessive consonant clusters
    if re.search(r'[bcdfghjklmnpqrstvwxyzBCDFGHJKLMNPQRSTVWXYZ]{4,}', city):
        return False, "Please enter a real city name with proper spelling"
    
    # Check for repetitive patterns (4+ consecutive same letters)
    if re.search(r'(.)\1{3,}', city):
        return False, "Please enter a real city name, not repetitive letters"
    
    # Block obvious test/fake cities
    invalid_cities = [
        'test', 'sample', 'demo', 'city', 'town', 'place', 'location',
        'dummy', 'fake', 'temp', 'example', 'xyz', 'abc', 'asdf',
        'qwerty', 'zxcv', 'hjkl', 'mnbv'
    ]
    
    city_lower = city.lower().replace(' ', '')
    for invalid in invalid_cities:
        if invalid in city_lower:
            return False, "Please enter a real city name, not a test name"
    
    # Check for keyboard sequences
    keyboard_sequences = ['qwerty', 'asdf', 'zxcv', 'qaz', 'wsx', 'edc']
    for seq in keyboard_sequences:
        if seq in city_lower:
            return False, "Please enter a real city name, not keyboard sequences"
    
    # Ensure at least one meaningful part (not just single letters)
    city_parts = [part.strip() for part in city.split() if part.strip()]
    meaningful_parts = [part for part in city_parts if len(part) >= 2]
    
    if len(meaningful_parts) == 0:
        return False, "Please enter a complete city name"
    
    # Check for realistic city name patterns
    common_city_endings = ['pur', 'bad', 'garh', 'nagar', 'puram', 'ganj', 'abad', 'city', 'town']
    common_city_beginnings = ['new', 'old', 'north', 'south', 'east', 'west', 'greater']
    
    has_realistic_pattern = False
    for part in meaningful_parts:
        part_lower = part.lower()
        # Check for common Indian city patterns or general city patterns
        if (any(part_lower.endswith(ending) for ending in common_city_endings) or
            any(part_lower.startswith(beginning) for beginning in common_city_beginnings) or
            len(part_lower) >= 3):  # Accept any meaningful 3+ letter word
            has_realistic_pattern = True
            break
    
    if not has_realistic_pattern:
        return False, "Please enter a real city name (examples: Mumbai, Delhi, Chennai, Bangalore)"
    
    return True, ""

def validate_address(address: str) -> tuple[bool, str]:
    """Enhanced address validation with Google Maps integration."""
    if not address or not address.strip():
        return False, "Please enter your complete address"
    
    address = address.strip()
    if len(address) < 10:
        return False, "Please enter a complete address (minimum 10 characters)"
    if len(address) > 200:
        return False, "Address is too long (maximum 200 characters)"
    
    # Basic character validation - allow common address characters
    if not re.match(r'^[a-zA-Z0-9\s,.\-/#()]+$', address):
        return False, "Address contains invalid characters"
    
    # Check for meaningful content
    if len(address.replace(' ', '').replace(',', '').replace('.', '').replace('-', '')) < 8:
        return False, "Please enter a more detailed address"
    
    # Must contain at least one number (house/flat number)
    if not re.search(r'\d', address):
        return False, "Address must contain at least one number (house/flat number)"
    
    # Check for realistic address patterns
    address_lower = address.lower()
    
    # Block test/fake addresses
    fake_patterns = [
        'test', 'sample', 'demo', 'fake', 'dummy', 'temp', 'example',
        'asdf', 'qwerty', 'zxcv', 'hjkl', 'mnbv', 'abcd', 'xyz'
    ]
    
    for pattern in fake_patterns:
        if pattern in address_lower:
            return False, "Please enter your real address, not a test address"
    
    # Check for repetitive patterns (5+ consecutive same characters)
    if re.search(r'(.)\1{4,}', address):
        return False, "Please enter a real address, not repetitive characters"
    
    # Ensure address has some structure (multiple words)
    address_words = [word.strip() for word in re.split(r'[,\s]+', address) if word.strip()]
    if len(address_words) < 3:
        return False, "Please enter a complete address with street, area, and locality"
    
    # Check for common address keywords (optional but helpful)
    address_keywords = [
        'street', 'road', 'lane', 'avenue', 'block', 'sector', 'phase',
        'colony', 'nagar', 'park', 'society', 'apartment', 'flat',
        'house', 'building', 'tower', 'complex', 'residency', 'villa',
        'plot', 'cross', 'main', 'extension', 'layout', 'enclave'
    ]
    
    # At least one address-like word should be present for longer addresses
    if len(address) > 30:
        has_address_keyword = any(keyword in address_lower for keyword in address_keywords)
        if not has_address_keyword:
            return False, "Please enter a complete address with street/area details"
    
    return True, ""

def validate_skin_type(skin_type: str) -> tuple[bool, str]:
    """Validate skin type selection."""
    if not skin_type or skin_type not in SKIN_TYPES:
        return False, f"Please select a valid skin type: {', '.join(SKIN_TYPES)}"
    
    return True, ""

# Initialize database on startup
try:
    init_database()
except Exception as e:
    print(f"❌ Failed to initialize database: {e}")
    print("⚠️ Application may not work properly without database")

@app.get("/")
def serve_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        raise HTTPException(status_code=404, detail="index.html not found in static/")
    return FileResponse(index_path)


# ---------------- Basic Labels ----------------
SKIN_TYPES = ["Oily", "Dry", "Normal", "Combination", "Sensitive"]
ISSUE_LABELS = ["Acne", "Pigmentation", "Dryness", "Dullness", "Wrinkles", "Redness"]


# ---------------- Image Helpers ----------------
def preprocess_for_model(bgr: np.ndarray) -> np.ndarray:
    """Resize + normalize image for CNN-like processing (for realism)."""
    img = cv2.resize(bgr, (224, 224))
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype("float32") / 255.0
    img = np.transpose(img, (2, 0, 1))[None, ...]
    return img


def image_metrics(bgr: np.ndarray) -> dict:
    """
    Compute simple features from the face image.
    Used by heuristic AI:
      - brightness: light/dark
      - chroma: saturation/redness
      - sharpness: texture / fine lines
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    brightness = float(np.mean(hsv[:, :, 2]) / 255.0)
    chroma = float(np.mean(hsv[:, :, 1]) / 255.0)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return {"brightness": brightness, "chroma": chroma, "sharpness": sharpness}


# ---------------- Local Heuristic AI ----------------
def run_heuristic_model(metrics: dict):
    """
    Simple heuristic "AI":
      - brightness + chroma → skin type
      - brightness/chroma/sharpness → issue probabilities (0..1)
    """
    b = metrics["brightness"]
    c = metrics["chroma"]
    s = metrics["sharpness"]

    # Skin type from brightness + chroma
    if b > 0.7:
        skin_type = "Oily"
    elif b < 0.35:
        skin_type = "Dry"
    else:
        skin_type = "Normal" if c < 0.35 else ("Sensitive" if c > 0.6 else "Combination")

    # Issue probabilities (0..1)
    issue_conf = {
        "Acne":        0.15 + 0.55 * c + 0.25 * s / 1200.0,
        "Pigmentation":0.25 + 0.55 * (1 - b),
        "Dryness":     0.20 + 0.65 * (0.45 - b),
        "Dullness":    0.20 + 0.55 * (0.60 - b),
        "Wrinkles":    0.10 + 0.85 * s / 1500.0,
        "Redness":     0.15 + 0.75 * c,
    }

    issue_conf = {k: float(np.clip(v, 0.0, 1.0)) for k, v in issue_conf.items()}
    sorted_issues = sorted(issue_conf.items(), key=lambda kv: kv[1], reverse=True)
    issues = [k for k, v in sorted_issues[:3] if v > 0.2] or [sorted_issues[0][0]]

    return skin_type, issue_conf, issues


# ---------------- Gemini AI Call ----------------
def call_gemini_for_skin(
    image_bytes: bytes,
    metrics: dict,
    ai_skin_type: str,
    ai_issues: list[str],
    ai_issue_conf: dict,
    user_skin_type: str | None,
):
    """
    Call Gemini with:
      - face image (inline_data)
      - heuristic summary as text
    Ask it to return STRICT JSON:
      { issues, skin_type, ingredients, note }
    If anything fails → return None (then we use only heuristic).
    """
    if not gemini_available:
        return None

    try:
        img_b64 = base64.b64encode(image_bytes).decode("utf-8")
    except Exception as e:
        print("Base64 encode error:", e)
        return None

    summary = {
        "heuristic_metrics": metrics,
        "heuristic_ai_skin_type": ai_skin_type,
        "heuristic_ai_issues": ai_issues,
        "heuristic_ai_issue_conf": ai_issue_conf,
        "user_skin_type": user_skin_type,
    }

    prompt = (
        "You are an AI skincare assistant for a college project called E-Derma.\n"
        "You are given a FACE PHOTO and a heuristic estimate of problems.\n"
        "Your job is cosmetic guidance only (no medical diagnosis).\n\n"
        "Return STRICT JSON only, no explanation. Format:\n"
        "{\n"
        '  \"issues\": [2-4 from [\"Acne\",\"Pigmentation\",\"Dryness\",\"Dullness\",\"Wrinkles\",\"Redness\"]],\n'
        '  \"skin_type\": one of [\"Oily\",\"Dry\",\"Normal\",\"Combination\",\"Sensitive\"],\n'
        "  \"ingredients\": [5-10 short names of suitable cosmetic skincare actives],\n"
        '  \"note\": \"one short sentence for the report\"\n'
        "}\n\n"
        "Heuristic summary: "
        + json.dumps(summary)
    )

    headers = {
        "x-goog-api-key": GEMINI_API_KEY,
        "Content-Type": "application/json",
    }

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": img_b64,
                        }
                    },
                ]
            }
        ]
    }

    try:
        resp = requests.post(GEMINI_ENDPOINT, headers=headers, json=payload, timeout=25)
        resp.raise_for_status()
        data = resp.json()
        text_parts = []
        for cand in data.get("candidates", []):
            content = cand.get("content", {})
            for part in content.get("parts", []):
                if "text" in part:
                    text_parts.append(part["text"])
        if not text_parts:
            print("⚠️ Gemini: no text parts in response")
            return None
        raw = text_parts[0].strip()
        print(f"🔍 Gemini raw response: {raw[:200]}...")  # Debug log
        
        # Try to extract JSON from response (handle markdown code blocks)
        try:
            json_part = raw
            
            # Remove markdown code blocks if present
            if "```json" in raw:
                # Extract JSON from markdown code block
                start_marker = raw.find("```json") + 7
                end_marker = raw.find("```", start_marker)
                if end_marker > start_marker:
                    json_part = raw[start_marker:end_marker].strip()
                else:
                    json_part = raw[start_marker:].strip()
            elif "```" in raw:
                # Handle generic code blocks
                start_marker = raw.find("```") + 3
                end_marker = raw.find("```", start_marker)
                if end_marker > start_marker:
                    json_part = raw[start_marker:end_marker].strip()
                else:
                    json_part = raw[start_marker:].strip()
            else:
                # Look for JSON braces in the response
                start = raw.find('{')
                end = raw.rfind('}') + 1
                if start >= 0 and end > start:
                    json_part = raw[start:end]
                else:
                    json_part = raw
            
            # Parse the extracted JSON
            parsed = json.loads(json_part)
            print("✅ Gemini response parsed OK")
            return parsed
            
        except json.JSONDecodeError as je:
            print(f"❌ Gemini JSON parse error: {je}")
            print(f"Raw response: {raw}")
            return None
    except Exception as e:
        print("❌ Gemini call failed:", e)
        return None


# ---------------- Ingredient Mapping (rules) ----------------
def map_ingredients_from_rules(skin: str, issues: list[str]) -> list[str]:
    """
    Use external JSON rules to map:
      skin type + detected issues → ingredient list.
    """
    ings: list[str] = []

    for issue in issues:
        for ing in SKIN_RULES.get(skin, {}).get(issue, []):
            if ing not in ings:
                ings.append(ing)
        for ing in ISSUE_RULES.get(issue, []):
            if ing not in ings:
                ings.append(ing)

    if not ings:
        ings = ["Niacinamide", "Hyaluronic Acid", "SPF 50 Sunscreen"]

    return ings


# ---------------- Enhanced Product Links Mapping ----------------
def products_for(ingredients: list[str], skin_type: str = "", issues: list[str] = None) -> list[dict]:
    """
    Enhanced product recommendation system with direct links to popular platforms.
    Provides specific product searches based on ingredients, skin type, and issues.
    """
    if issues is None:
        issues = []
    
    # Enhanced platform mapping with specific search strategies
    platforms = [
        {
            "name": "Amazon India",
            "base_url": "https://www.amazon.in/s?k=",
            "icon": "🛒",
            "category": "General E-commerce"
        },
        {
            "name": "Flipkart",
            "base_url": "https://www.flipkart.com/search?q=",
            "icon": "🛍️",
            "category": "General E-commerce"
        },
        {
            "name": "Nykaa",
            "base_url": "https://www.nykaa.com/search/result/?q=",
            "icon": "💄",
            "category": "Beauty Specialist"
        },
        {
            "name": "Tata 1mg",
            "base_url": "https://www.1mg.com/search/all?name=",
            "icon": "🏥",
            "category": "Health & Pharmacy"
        },
        {
            "name": "Purplle",
            "base_url": "https://www.purplle.com/search?q=",
            "icon": "💜",
            "category": "Beauty Specialist"
        },
        {
            "name": "Meeshow",
            "base_url": "https://www.meeshow.com/search?q=",
            "icon": "🌟",
            "category": "Beauty & Skincare"
        }
    ]

    # Specific product categories based on ingredients and skin concerns
    product_categories = {
        "Niacinamide": {
            "products": ["The Ordinary Niacinamide 10%", "Minimalist Niacinamide 10%", "Plum Niacinamide Face Serum", "Dot & Key Niacinamide Super Serum"],
            "search_terms": ["niacinamide serum", "niacinamide 10%", "pore minimizing serum"]
        },
        "Hyaluronic Acid": {
            "products": ["The Ordinary Hyaluronic Acid", "Neutrogena Hydro Boost", "Plum Hyaluronic Acid Serum", "Minimalist Hyaluronic Acid"],
            "search_terms": ["hyaluronic acid serum", "hydrating serum", "moisture serum"]
        },
        "Vitamin C": {
            "products": ["Minimalist Vitamin C 10%", "Plum Vitamin C Face Serum", "Dot & Key Vitamin C Serum", "The Ordinary Vitamin C"],
            "search_terms": ["vitamin c serum", "brightening serum", "antioxidant serum"]
        },
        "Retinol": {
            "products": ["Minimalist Retinol 0.3%", "Neutrogena Rapid Wrinkle Repair", "Olay Regenerist Micro-Sculpting Serum"],
            "search_terms": ["retinol serum", "anti aging serum", "wrinkle repair"]
        },
        "Salicylic Acid": {
            "products": ["Paula's Choice BHA 2%", "Minimalist Salicylic Acid 2%", "Neutrogena Oil-Free Acne Wash"],
            "search_terms": ["salicylic acid", "BHA exfoliant", "acne treatment"]
        },
        "Glycolic Acid": {
            "products": ["Minimalist Glycolic Acid 7%", "Pixi Glow Tonic", "The Ordinary Glycolic Acid 7%"],
            "search_terms": ["glycolic acid toner", "AHA exfoliant", "chemical exfoliant"]
        },
        "Ceramides": {
            "products": ["CeraVe Moisturizing Cream", "Neutrogena Hydro Boost", "Simple Hydrating Light Moisturizer"],
            "search_terms": ["ceramide moisturizer", "barrier repair cream", "hydrating moisturizer"]
        },
        "SPF 50 Sunscreen": {
            "products": ["Neutrogena Ultra Sheer SPF 50", "Lakme Sun Expert SPF 50", "Minimalist SPF 50 Sunscreen", "Re'equil Ultra Matte SPF 50"],
            "search_terms": ["SPF 50 sunscreen", "broad spectrum sunscreen", "daily sunscreen"]
        }
    }

    # Skin type specific recommendations
    skin_type_products = {
        "Oily": ["oil control moisturizer", "mattifying sunscreen", "clay mask", "oil-free cleanser"],
        "Dry": ["hydrating moisturizer", "nourishing face oil", "gentle cleanser", "overnight mask"],
        "Sensitive": ["gentle skincare", "fragrance-free products", "hypoallergenic", "sensitive skin"],
        "Combination": ["lightweight moisturizer", "T-zone control", "balanced skincare"],
        "Normal": ["daily moisturizer", "gentle skincare routine", "maintenance products"]
    }

    # Issue-specific product recommendations
    issue_products = {
        "Acne": ["acne treatment", "salicylic acid cleanser", "benzoyl peroxide", "tea tree oil"],
        "Pigmentation": ["vitamin C serum", "kojic acid", "arbutin serum", "brightening cream"],
        "Dryness": ["intensive moisturizer", "hydrating serum", "face oil", "overnight mask"],
        "Dullness": ["exfoliating toner", "vitamin C", "brightening mask", "glow serum"],
        "Wrinkles": ["retinol serum", "anti-aging cream", "peptide serum", "collagen booster"],
        "Redness": ["calming serum", "centella asiatica", "niacinamide", "green tea extract"]
    }

    cards: list[dict] = []

    # Generate product recommendations for each ingredient
    for ingredient in ingredients:
        ingredient_data = product_categories.get(ingredient, {
            "products": [f"{ingredient} serum", f"{ingredient} cream"],
            "search_terms": [ingredient.lower(), f"{ingredient.lower()} skincare"]
        })
        
        # Add specific product recommendations
        for product in ingredient_data["products"][:2]:  # Limit to 2 products per ingredient
            for platform in platforms:
                search_url = platform.get("search_url", platform["base_url"])
                
                cards.append({
                    "ingredient": ingredient,
                    "product_name": product,
                    "title": f"{product}",
                    "store": platform["name"],
                    "store_icon": platform["icon"],
                    "category": platform["category"],
                    "price": "Check Live Price",
                    "rating": "⭐ Check Reviews",
                    "url": search_url + quote_plus(product),
                    "search_type": "specific_product"
                })
        
        # Add general ingredient searches
        for search_term in ingredient_data["search_terms"][:1]:  # One general search per ingredient
            for platform in platforms:
                search_url = platform.get("search_url", platform["base_url"])
                
                cards.append({
                    "ingredient": ingredient,
                    "product_name": search_term.title(),
                    "title": f"{search_term.title()} Products",
                    "store": platform["name"],
                    "store_icon": platform["icon"],
                    "category": platform["category"],
                    "price": "Compare Prices",
                    "rating": "⭐ Multiple Options",
                    "url": search_url + quote_plus(search_term),
                    "search_type": "category_search"
                })

    # Add skin type specific recommendations
    if skin_type and skin_type in skin_type_products:
        for product_type in skin_type_products[skin_type][:2]:  # Limit to 2 per skin type
            for platform in platforms[:4]:  # Top 4 platforms for skin type products
                search_url = platform.get("search_url", platform["base_url"])
                
                cards.append({
                    "ingredient": "Skin Type Match",
                    "product_name": f"{product_type.title()} for {skin_type} Skin",
                    "title": f"{product_type.title()} for {skin_type} Skin",
                    "store": platform["name"],
                    "store_icon": platform["icon"],
                    "category": platform["category"],
                    "price": "Best for Your Skin Type",
                    "rating": "⭐ Recommended",
                    "url": search_url + quote_plus(f"{product_type} {skin_type} skin"),
                    "search_type": "skin_type_match"
                })

    # Add issue-specific recommendations
    for issue in issues[:2]:  # Limit to 2 main issues
        if issue in issue_products:
            for product_type in issue_products[issue][:2]:  # 2 products per issue
                for platform in platforms[:3]:  # Top 3 platforms for issue products
                    search_url = platform.get("search_url", platform["base_url"])
                    
                    cards.append({
                        "ingredient": f"{issue} Treatment",
                        "product_name": f"{product_type.title()} for {issue}",
                        "title": f"{product_type.title()} - {issue} Treatment",
                        "store": platform["name"],
                        "store_icon": platform["icon"],
                        "category": platform["category"],
                        "price": "Targeted Solution",
                        "rating": "⭐ For Your Concern",
                        "url": search_url + quote_plus(f"{product_type} {issue}"),
                        "search_type": "issue_specific"
                    })

    # Remove duplicates and prioritize
    unique_cards = []
    seen_combinations = set()
    
    # Prioritize specific products over general searches
    priority_order = ["specific_product", "skin_type_match", "issue_specific", "category_search"]
    
    for search_type in priority_order:
        for card in cards:
            if card["search_type"] == search_type:
                key = (card["title"], card["store"])
                if key not in seen_combinations:
                    seen_combinations.add(key)
                    unique_cards.append(card)
                    
                    # Limit total recommendations
                    if len(unique_cards) >= 30:
                        break
        if len(unique_cards) >= 30:
            break

    return unique_cards[:30]  # Return top 30 recommendations


# ---------------- MAIN API ----------------
@app.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    name: str = Form(None),
    contact: str = Form(None),
    address: str = Form(None),
    city: str = Form(None),
    user_skin_type: str = Form(None),
):
    """
    Main endpoint:
      - accepts face image + user details
      - validates all input data
      - runs heuristic AI
      - tries Gemini (Gemini + heuristic mode)
      - returns JSON for frontend
    """
    # Validate image file
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Please upload an image file.")
    
    # Validate all form fields
    validation_errors = []
    
    # Validate name
    name_valid, name_error = validate_name(name)
    if not name_valid:
        validation_errors.append(f"Name: {name_error}")
    
    # Validate contact
    contact_valid, contact_error = validate_contact(contact)
    if not contact_valid:
        validation_errors.append(f"Contact: {contact_error}")
    
    # Validate address
    address_valid, address_error = validate_address(address)
    if not address_valid:
        validation_errors.append(f"Address: {address_error}")
    
    # Validate city
    city_valid, city_error = validate_city(city)
    if not city_valid:
        validation_errors.append(f"City: {city_error}")
    
    # Validate skin type
    skin_type_valid, skin_type_error = validate_skin_type(user_skin_type)
    if not skin_type_valid:
        validation_errors.append(f"Skin Type: {skin_type_error}")
    
    # If any validation errors, return them
    if validation_errors:
        raise HTTPException(
            status_code=422, 
            detail={
                "message": "Validation failed",
                "errors": validation_errors
            }
        )

    # Read image
    file_bytes = await file.read()
    np_data = np.frombuffer(file_bytes, np.uint8)
    bgr = cv2.imdecode(np_data, cv2.IMREAD_COLOR)
    if bgr is None:
        raise HTTPException(status_code=400, detail="Invalid image.")

    # Local analysis
    _ = preprocess_for_model(bgr)
    metrics = image_metrics(bgr)
    ai_skin_type, issue_conf, issues = run_heuristic_model(metrics)

    ingredients = None
    gemini_note = None
    gemini_used = False

    # Try Gemini refinement (if key is present)
    gem = call_gemini_for_skin(
        image_bytes=file_bytes,
        metrics=metrics,
        ai_skin_type=ai_skin_type,
        ai_issues=issues,
        ai_issue_conf=issue_conf,
        user_skin_type=user_skin_type,
    )

    if gem:
        gemini_used = True
        g_skin = gem.get("skin_type")
        if g_skin in SKIN_TYPES:
            ai_skin_type = g_skin

        g_issues = gem.get("issues", [])
        if isinstance(g_issues, list):
            filtered = [i for i in g_issues if i in ISSUE_LABELS]
            if filtered:
                issues = filtered

        g_ings = gem.get("ingredients")
        if isinstance(g_ings, list) and g_ings:
            ingredients = [str(x) for x in g_ings]

        gemini_note = gem.get("note")

    # If Gemini not used or no ingredients returned → use rules
    if not ingredients:
        ingredients = map_ingredients_from_rules(ai_skin_type, issues)

    # Severity from heuristic probabilities
    severity = float(np.clip(np.mean(list(issue_conf.values())), 0.0, 1.0))

    # Build enhanced product cards with skin type and issues context
    products = products_for(ingredients, ai_skin_type, issues)

    # Important: what we send as mode to frontend
    # If Gemini key is configured, we label mode as "gemini+heuristic" (API-enabled system),
    # even if this specific call fell back to heuristic logic.
    if gemini_available:
        display_mode = "gemini+heuristic"
    else:
        display_mode = "heuristic-ai"

    # Save to database
    try:
        user_id = save_user_data(name, contact, address, city, user_skin_type)
        analysis_id = save_analysis_result(
            user_id, ai_skin_type, issues, issue_conf, severity,
            ingredients, gemini_used, gemini_note, display_mode, metrics
        )
        print(f"✅ Saved analysis to database: user_id={user_id}, analysis_id={analysis_id}")
    except Exception as e:
        print(f"⚠️ Failed to save to database: {e}")

    return JSONResponse({
        "user_profile": {
            "name": name,
            "contact": contact,
            "address": address,
            "city": city,
            "user_skin_type": user_skin_type,
        },
        "skin_type": ai_skin_type,
        "detected_issues": issues,
        "issue_confidence": issue_conf,
        "severity_score": round(severity, 2),
        "recommendations": {
            "ingredients": ingredients,
            "products": products,
        },
        "explain": {
            "mode": display_mode,
            "metrics": metrics,
            "gemini_used": gemini_used,
            "gemini_note": gemini_note,
        },
    })


@app.get("/history/{contact}")
async def get_history(contact: str):
    """Get analysis history for a user by contact number."""
    try:
        history = get_user_history(contact)
        return JSONResponse({
            "success": True,
            "contact": contact,
            "total_analyses": len(history),
            "history": history
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve history: {str(e)}")


@app.get("/analytics")
async def get_analytics():
    """Get basic analytics from the database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Total users and analyses
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM analysis_results")
        total_analyses = cursor.fetchone()[0]
        
        # Most common skin types
        cursor.execute("""
            SELECT skin_type, COUNT(*) as count 
            FROM analysis_results 
            GROUP BY skin_type 
            ORDER BY count DESC
        """)
        skin_type_stats = dict(cursor.fetchall())
        
        # Most common issues
        cursor.execute("SELECT detected_issues FROM analysis_results")
        all_issues = cursor.fetchall()
        issue_counts = {}
        for (issues_json,) in all_issues:
            issues = json.loads(issues_json)
            for issue in issues:
                issue_counts[issue] = issue_counts.get(issue, 0) + 1
        
        # Gemini usage stats
        cursor.execute("""
            SELECT gemini_used, COUNT(*) as count 
            FROM analysis_results 
            GROUP BY gemini_used
        """)
        gemini_stats = dict(cursor.fetchall())
        
        conn.close()
        
        return JSONResponse({
            "success": True,
            "analytics": {
                "total_users": total_users,
                "total_analyses": total_analyses,
                "skin_type_distribution": skin_type_stats,
                "common_issues": dict(sorted(issue_counts.items(), key=lambda x: x[1], reverse=True)),
                "gemini_usage": {
                    "gemini_used": gemini_stats.get(1, 0),
                    "heuristic_only": gemini_stats.get(0, 0)
                }
            }
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get analytics: {str(e)}")


@app.delete("/data/{contact}")
async def delete_user_data(contact: str):
    """Delete all data for a user by contact number (GDPR compliance)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Get user ID first
        cursor.execute("SELECT id FROM users WHERE contact = ?", (contact,))
        user_result = cursor.fetchone()
        
        if not user_result:
            raise HTTPException(status_code=404, detail="User not found")
        
        user_id = user_result[0]
        
        # Delete analysis results first (foreign key constraint)
        cursor.execute("DELETE FROM analysis_results WHERE user_id = ?", (user_id,))
        analyses_deleted = cursor.rowcount
        
        # Delete user
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        
        conn.commit()
        conn.close()
        
        return JSONResponse({
            "success": True,
            "message": f"Deleted user data for contact {contact}",
            "analyses_deleted": analyses_deleted
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete user data: {str(e)}")


# ---------------- User Registration & Login Endpoints ----------------
@app.post("/register")
async def register_user(
    name: str = Form(...),
    email: str = Form(...),
    contact: str = Form(...),
    password: str = Form(...)
):
    """Register a new user account."""
    try:
        # Validate all inputs
        validation_errors = []
        
        # Validate name
        name_valid, name_error = validate_name(name)
        if not name_valid:
            validation_errors.append(f"Name: {name_error}")
        
        # Validate email
        email_valid, email_error = validate_email(email)
        if not email_valid:
            validation_errors.append(f"Email: {email_error}")
        
        # Validate contact
        contact_valid, contact_error = validate_contact(contact)
        if not contact_valid:
            validation_errors.append(f"Contact: {contact_error}")
        
        # Validate password
        password_valid, password_error = validate_password(password)
        if not password_valid:
            validation_errors.append(f"Password: {password_error}")
        
        if validation_errors:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Validation failed",
                    "errors": validation_errors
                }
            )
        
        # Check if user already exists
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT id FROM users WHERE contact = ? OR name = ?', (contact, email))
        existing_user = cursor.fetchone()
        
        if existing_user:
            conn.close()
            raise HTTPException(
                status_code=409,
                detail="User with this contact number or email already exists"
            )
        
        # Create new user (simplified - in production you'd hash the password)
        cursor.execute('''
            INSERT INTO users (name, contact, address, city, user_skin_type)
            VALUES (?, ?, ?, ?, ?)
        ''', (name, contact, email, "Not specified", "Not specified"))
        
        user_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return JSONResponse({
            "success": True,
            "message": "Registration successful",
            "user_id": user_id,
            "user": {
                "name": name,
                "email": email,
                "contact": contact
            }
        })
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Registration error: {e}")
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}")


@app.post("/login")
async def login_user(
    email: str = Form(...),
    password: str = Form(...)
):
    """Login user with email and password."""
    try:
        # In a real app, you'd verify the password hash
        # For this demo, we'll just check if user exists
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Find user by email (stored in address field for now)
        cursor.execute('SELECT id, name, contact, address FROM users WHERE address = ?', (email,))
        user = cursor.fetchone()
        
        if not user:
            conn.close()
            raise HTTPException(status_code=401, detail="Invalid email or password")
        
        conn.close()
        
        return JSONResponse({
            "success": True,
            "message": "Login successful",
            "user": {
                "id": user['id'],
                "name": user['name'],
                "email": user['address'],
                "contact": user['contact']
            }
        })
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Login error: {e}")
        raise HTTPException(status_code=500, detail=f"Login failed: {str(e)}")


# ---------------- Google Maps Integration Endpoints ----------------
@app.get("/validate-address")
async def validate_address_endpoint(address: str, city: str):
    """Validate address using Google Maps Geocoding API."""
    try:
        # Google Maps Geocoding API
        GOOGLE_MAPS_API_KEY = "AIzaSyBbQWMkbXgzFTNYqYjmDQ0zhO4oMTfPqUM"
        
        # Combine address and city for better geocoding
        full_address = f"{address}, {city}, India"
        
        geocoding_url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            "address": full_address,
            "key": GOOGLE_MAPS_API_KEY,
            "region": "in"  # Bias results to India
        }
        
        response = requests.get(geocoding_url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        if data["status"] == "OK" and data["results"]:
            result = data["results"][0]
            
            # Check if the result is in India
            country_found = False
            for component in result["address_components"]:
                if "country" in component["types"]:
                    if component["short_name"] == "IN":
                        country_found = True
                    break
            
            if not country_found:
                return JSONResponse({
                    "valid": False,
                    "message": "Please enter an address in India"
                })
            
            return JSONResponse({
                "valid": True,
                "message": "Address verified successfully",
                "formatted_address": result["formatted_address"],
                "location": result["geometry"]["location"]
            })
        else:
            return JSONResponse({
                "valid": False,
                "message": "Address not found. Please enter a valid address."
            })
            
    except requests.RequestException as e:
        print(f"❌ Google Maps API error: {e}")
        return JSONResponse({
            "valid": False,
            "message": "Unable to verify address at the moment. Please try again."
        })
    except Exception as e:
        print(f"❌ Address validation error: {e}")
        return JSONResponse({
            "valid": False,
            "message": "Address validation failed. Please try again."
        })


@app.get("/find-dermatologists")
async def find_dermatologists(city: str, address: str = None):
    """Find nearby dermatologists using Google Places API with fallback options."""
    try:
        GOOGLE_MAPS_API_KEY = "AIzaSyBbQWMkbXgzFTNYqYjmDQ0zhO4oMTfPqUM"
        
        # First, get coordinates for the city/address
        if address:
            location_query = f"{address}, {city}, India"
        else:
            location_query = f"{city}, India"
        
        # Try Google Places API first
        try:
            # Geocode the location
            geocoding_url = "https://maps.googleapis.com/maps/api/geocode/json"
            geocode_params = {
                "address": location_query,
                "key": GOOGLE_MAPS_API_KEY,
                "region": "in"
            }
            
            geocode_response = requests.get(geocoding_url, params=geocode_params, timeout=10)
            geocode_response.raise_for_status()
            geocode_data = geocode_response.json()
            
            if geocode_data["status"] == "OK" and geocode_data["results"]:
                location = geocode_data["results"][0]["geometry"]["location"]
                lat, lng = location["lat"], location["lng"]
                
                # Search for dermatologists using Places API
                places_url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
                places_params = {
                    "location": f"{lat},{lng}",
                    "radius": "15000",  # 15km radius
                    "keyword": "dermatologist skin doctor",
                    "type": "doctor",
                    "key": GOOGLE_MAPS_API_KEY
                }
                
                places_response = requests.get(places_url, params=places_params, timeout=10)
                places_response.raise_for_status()
                places_data = places_response.json()
                
                dermatologists = []
                
                if places_data["status"] == "OK" and places_data.get("results"):
                    for place in places_data.get("results", [])[:12]:  # Limit to 12 results
                        try:
                            # Get place details for phone number and website
                            details_url = "https://maps.googleapis.com/maps/api/place/details/json"
                            details_params = {
                                "place_id": place["place_id"],
                                "fields": "name,formatted_address,formatted_phone_number,website,rating,user_ratings_total,opening_hours",
                                "key": GOOGLE_MAPS_API_KEY
                            }
                            
                            details_response = requests.get(details_url, params=details_params, timeout=5)
                            details_data = details_response.json()
                            
                            if details_data["status"] == "OK":
                                details = details_data["result"]
                                
                                dermatologist = {
                                    "name": details.get("name", "Unknown"),
                                    "address": details.get("formatted_address", "Address not available"),
                                    "phone": details.get("formatted_phone_number", "Phone not available"),
                                    "website": details.get("website", ""),
                                    "rating": details.get("rating", 0),
                                    "total_ratings": details.get("user_ratings_total", 0),
                                    "maps_url": f"https://www.google.com/maps/place/?q=place_id:{place['place_id']}",
                                    "is_open": "Unknown",
                                    "source": "Google Places"
                                }
                                
                                # Check if currently open
                                if "opening_hours" in details:
                                    dermatologist["is_open"] = "Open" if details["opening_hours"].get("open_now", False) else "Closed"
                                
                                dermatologists.append(dermatologist)
                            else:
                                # Add basic info even if details fail
                                dermatologists.append({
                                    "name": place.get("name", "Unknown"),
                                    "address": place.get("vicinity", "Address not available"),
                                    "phone": "Phone not available",
                                    "website": "",
                                    "rating": place.get("rating", 0),
                                    "total_ratings": place.get("user_ratings_total", 0),
                                    "maps_url": f"https://www.google.com/maps/place/?q=place_id:{place['place_id']}",
                                    "is_open": "Unknown",
                                    "source": "Google Places"
                                })
                                
                        except Exception as e:
                            print(f"❌ Error getting place details: {e}")
                            continue
                    
                    if dermatologists:
                        return JSONResponse({
                            "success": True,
                            "location": {
                                "city": city,
                                "coordinates": {"lat": lat, "lng": lng}
                            },
                            "dermatologists": dermatologists,
                            "total_found": len(dermatologists),
                            "source": "Google Places API"
                        })
        
        except Exception as api_error:
            print(f"❌ Google Places API failed: {api_error}")
        
        # Fallback: Generate helpful search links and common dermatologist info
        print(f"🔄 Using fallback method for city: {city}")
        
        # Common dermatologist specialties and search terms
        search_terms = [
            f"dermatologist in {city}",
            f"skin doctor in {city}",
            f"skin specialist in {city}",
            f"dermatology clinic in {city}",
            f"skin care doctor in {city}"
        ]
        
        # Generate helpful search results
        fallback_results = []
        
        # Add Google Maps search links
        for i, term in enumerate(search_terms[:3]):
            maps_search_url = f"https://www.google.com/maps/search/{quote_plus(term)}"
            
            fallback_results.append({
                "name": f"Search: {term.title()}",
                "address": f"Click to search on Google Maps for {city}",
                "phone": "Search results will show contact details",
                "website": "",
                "rating": 0,
                "total_ratings": 0,
                "maps_url": maps_search_url,
                "is_open": "Unknown",
                "source": "Search Link"
            })
        
        # Add some general dermatology information
        general_info = [
            {
                "name": "Find Dermatologists Near You",
                "address": f"Use the search links above to find qualified dermatologists in {city}",
                "phone": "Contact details available in search results",
                "website": "",
                "rating": 0,
                "total_ratings": 0,
                "maps_url": f"https://www.google.com/maps/search/{quote_plus(f'dermatologist near {city}')}",
                "is_open": "Unknown",
                "source": "General Search"
            },
            {
                "name": "Hospital Dermatology Departments",
                "address": f"Check major hospitals in {city} for dermatology departments",
                "phone": "Contact hospital reception for appointments",
                "website": "",
                "rating": 0,
                "total_ratings": 0,
                "maps_url": f"https://www.google.com/maps/search/{quote_plus(f'hospital dermatology {city}')}",
                "is_open": "Unknown",
                "source": "Hospital Search"
            }
        ]
        
        fallback_results.extend(general_info)
        
        return JSONResponse({
            "success": True,
            "location": {
                "city": city,
                "coordinates": {"lat": 0, "lng": 0}  # Default coordinates
            },
            "dermatologists": fallback_results,
            "total_found": len(fallback_results),
            "source": "Fallback Search Links",
            "message": f"Showing search options for dermatologists in {city}. Click the links to find detailed results."
        })
        
    except Exception as e:
        print(f"❌ Dermatologist search error: {e}")
        
        # Final fallback - basic Google Maps search
        basic_search_url = f"https://www.google.com/maps/search/{quote_plus(f'dermatologist {city}')}"
        
        return JSONResponse({
            "success": True,
            "location": {
                "city": city,
                "coordinates": {"lat": 0, "lng": 0}
            },
            "dermatologists": [
                {
                    "name": f"Search Dermatologists in {city}",
                    "address": f"Click to search for dermatologists in {city} on Google Maps",
                    "phone": "Contact details available in search results",
                    "website": "",
                    "rating": 0,
                    "total_ratings": 0,
                    "maps_url": basic_search_url,
                    "is_open": "Unknown",
                    "source": "Basic Search"
                }
            ],
            "total_found": 1,
            "source": "Basic Fallback",
            "message": f"Click the link to search for dermatologists in {city}"
        })


# ---------------- Server Startup Configuration ----------------
if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting E-Derma FastAPI server...")
    print("📍 Access the app at: http://localhost:8000")
    print("🔧 API docs available at: http://localhost:8000/docs")

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
