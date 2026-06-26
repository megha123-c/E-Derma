#!/usr/bin/env python3
"""
Web-based database viewer for E-Derma
Run this to view your data in a web browser
"""

import sqlite3
import json
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

# Database path
DB_PATH = "skin_analysis.db"

# Create FastAPI app for database viewer
viewer_app = FastAPI(title="E-Derma Database Viewer")

def get_db_data():
    """Get all data from database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get users
        cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
        users = [dict(row) for row in cursor.fetchall()]
        
        # Get analysis results with user names
        cursor.execute("""
            SELECT ar.*, u.name as user_name, u.contact as user_contact
            FROM analysis_results ar
            JOIN users u ON ar.user_id = u.id
            ORDER BY ar.created_at DESC
        """)
        analyses = []
        for row in cursor.fetchall():
            analysis = dict(row)
            # Parse JSON fields
            try:
                analysis['detected_issues'] = json.loads(analysis['detected_issues'])
                analysis['issue_confidence'] = json.loads(analysis['issue_confidence'])
                analysis['ingredients'] = json.loads(analysis['ingredients'])
                analysis['image_metrics'] = json.loads(analysis['image_metrics'])
            except:
                pass
            analyses.append(analysis)
        
        # Get statistics
        cursor.execute("SELECT COUNT(*) as count FROM users")
        user_count = cursor.fetchone()['count']
        
        cursor.execute("SELECT COUNT(*) as count FROM analysis_results")
        analysis_count = cursor.fetchone()['count']
        
        cursor.execute("SELECT COUNT(*) as count FROM analysis_results WHERE gemini_used = 1")
        gemini_count = cursor.fetchone()['count']
        
        conn.close()
        
        return {
            'users': users,
            'analyses': analyses,
            'stats': {
                'total_users': user_count,
                'total_analyses': analysis_count,
                'gemini_used': gemini_count,
                'heuristic_only': analysis_count - gemini_count
            }
        }
    except Exception as e:
        print(f"Error getting database data: {e}")
        return {'users': [], 'analyses': [], 'stats': {}}

@viewer_app.get("/", response_class=HTMLResponse)
async def view_database():
    """Main database viewer page."""
    data = get_db_data()
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>E-Derma Database Viewer</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
            .header {{ background: #22c55e; color: white; padding: 20px; border-radius: 10px; margin-bottom: 20px; }}
            .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 30px; }}
            .stat-card {{ background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); text-align: center; }}
            .stat-number {{ font-size: 2em; font-weight: bold; color: #22c55e; }}
            .section {{ background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); margin-bottom: 20px; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
            th {{ background-color: #f8f9fa; font-weight: bold; }}
            .json-data {{ background: #f8f9fa; padding: 10px; border-radius: 5px; font-family: monospace; font-size: 0.9em; }}
            .refresh-btn {{ background: #22c55e; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; }}
            .refresh-btn:hover {{ background: #16a34a; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🔬 E-Derma Database Viewer</h1>
                <p>Real-time view of your skin analysis application data</p>
                <button class="refresh-btn" onclick="location.reload()">🔄 Refresh Data</button>
            </div>
            
            <div class="stats">
                <div class="stat-card">
                    <div class="stat-number">{data['stats'].get('total_users', 0)}</div>
                    <div>Total Users</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number">{data['stats'].get('total_analyses', 0)}</div>
                    <div>Total Analyses</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number">{data['stats'].get('gemini_used', 0)}</div>
                    <div>Gemini AI Used</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number">{data['stats'].get('heuristic_only', 0)}</div>
                    <div>Heuristic Only</div>
                </div>
            </div>
            
            <div class="section">
                <h2>👥 Registered Users ({len(data['users'])})</h2>
                <table>
                    <tr>
                        <th>ID</th>
                        <th>Name</th>
                        <th>Contact</th>
                        <th>City</th>
                        <th>Skin Type</th>
                        <th>Registered</th>
                    </tr>
    """
    
    for user in data['users']:
        created_at = user['created_at'][:19] if user['created_at'] else 'N/A'
        html += f"""
                    <tr>
                        <td>{user['id']}</td>
                        <td>{user['name']}</td>
                        <td>{user['contact']}</td>
                        <td>{user['city']}</td>
                        <td>{user['user_skin_type']}</td>
                        <td>{created_at}</td>
                    </tr>
        """
    
    html += """
                </table>
            </div>
            
            <div class="section">
                <h2>🔬 Analysis Results ({len(data['analyses'])})</h2>
                <table>
                    <tr>
                        <th>ID</th>
                        <th>User</th>
                        <th>Skin Type</th>
                        <th>Issues</th>
                        <th>Severity</th>
                        <th>Ingredients</th>
                        <th>AI Mode</th>
                        <th>Date</th>
                    </tr>
    """
    
    for analysis in data['analyses']:
        issues = ', '.join(analysis['detected_issues'][:2]) if isinstance(analysis['detected_issues'], list) else str(analysis['detected_issues'])[:30]
        ingredients = ', '.join(analysis['ingredients'][:3]) if isinstance(analysis['ingredients'], list) else str(analysis['ingredients'])[:30]
        created_at = analysis['created_at'][:19] if analysis['created_at'] else 'N/A'
        ai_mode = "🤖 Gemini" if analysis['gemini_used'] else "🧠 Heuristic"
        
        html += f"""
                    <tr>
                        <td>{analysis['id']}</td>
                        <td>{analysis['user_name']}</td>
                        <td>{analysis['skin_type']}</td>
                        <td>{issues}...</td>
                        <td>{analysis['severity_score']:.2f}</td>
                        <td>{ingredients}...</td>
                        <td>{ai_mode}</td>
                        <td>{created_at}</td>
                    </tr>
        """
    
    html += f"""
                </table>
            </div>
            
            <div class="section">
                <h2>📊 Database File Location</h2>
                <p><strong>File:</strong> <code>{DB_PATH}</code></p>
                <p><strong>Full Path:</strong> <code>{os.path.abspath(DB_PATH)}</code></p>
                <p><strong>File Size:</strong> {os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0} bytes</p>
            </div>
            
            <div class="section">
                <h2>🔧 How to Access Data</h2>
                <div class="json-data">
# Method 1: Command line viewer
python view_database.py

# Method 2: SQLite command line
sqlite3 {DB_PATH}

# Method 3: API endpoints
GET http://localhost:8000/analytics
GET http://localhost:8000/history/CONTACT_NUMBER

# Method 4: This web viewer
python database_viewer.py
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    
    return html

if __name__ == "__main__":
    import os
    print("🔍 Starting E-Derma Database Viewer...")
    print(f"📊 Database file: {os.path.abspath(DB_PATH)}")
    print("🌐 Open browser: http://localhost:8001")
    print("🔄 Refresh the page to see latest data")
    print("⏹️  Press Ctrl+C to stop")
    
    uvicorn.run(viewer_app, host="0.0.0.0", port=8001)