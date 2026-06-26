#!/usr/bin/env python3
"""
Simple script to view E-Derma database contents
"""
import sqlite3
import json
from datetime import datetime

DB_PATH = "skin_analysis.db"

def view_database():
    """View all data in the E-Derma database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        print("=" * 60)
        print("E-DERMA DATABASE VIEWER")
        print("=" * 60)
        
        # Check if tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        
        if not tables:
            print("❌ No tables found. Database might not be initialized.")
            return
            
        print(f"📊 Found tables: {[table[0] for table in tables]}")
        print()
        
        # View Users Table
        print("👥 USERS TABLE:")
        print("-" * 40)
        cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
        users = cursor.fetchall()
        
        if users:
            print(f"{'ID':<3} {'Name':<15} {'Contact':<12} {'City':<12} {'Skin Type':<12} {'Created':<19}")
            print("-" * 80)
            for user in users:
                created = user[6][:19] if user[6] else "N/A"  # Truncate timestamp
                print(f"{user[0]:<3} {user[1]:<15} {user[2]:<12} {user[4]:<12} {user[5]:<12} {created:<19}")
        else:
            print("No users found.")
        
        print()
        
        # View Analysis Results Table
        print("🔬 ANALYSIS RESULTS TABLE:")
        print("-" * 40)
        cursor.execute("""
            SELECT ar.id, u.name, ar.skin_type, ar.detected_issues, 
                   ar.severity_score, ar.gemini_used, ar.created_at
            FROM analysis_results ar
            JOIN users u ON ar.user_id = u.id
            ORDER BY ar.created_at DESC
        """)
        analyses = cursor.fetchall()
        
        if analyses:
            print(f"{'ID':<3} {'User':<15} {'Skin Type':<12} {'Issues':<20} {'Severity':<8} {'Gemini':<6} {'Date':<19}")
            print("-" * 90)
            for analysis in analyses:
                issues = json.loads(analysis[3])[:2] if analysis[3] else []  # Show first 2 issues
                issues_str = ", ".join(issues)[:18] + "..." if len(", ".join(issues)) > 18 else ", ".join(issues)
                created = analysis[6][:19] if analysis[6] else "N/A"
                gemini = "Yes" if analysis[5] else "No"
                print(f"{analysis[0]:<3} {analysis[1]:<15} {analysis[2]:<12} {issues_str:<20} {analysis[4]:<8.2f} {gemini:<6} {created:<19}")
        else:
            print("No analysis results found.")
        
        print()
        
        # Statistics
        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM analysis_results")
        analysis_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM analysis_results WHERE gemini_used = 1")
        gemini_count = cursor.fetchone()[0]
        
        print("📈 STATISTICS:")
        print("-" * 20)
        print(f"Total Users: {user_count}")
        print(f"Total Analyses: {analysis_count}")
        print(f"Gemini API Used: {gemini_count}")
        print(f"Heuristic Only: {analysis_count - gemini_count}")
        
        conn.close()
        
    except sqlite3.Error as e:
        print(f"❌ Database error: {e}")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    view_database()