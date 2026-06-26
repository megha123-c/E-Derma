#!/usr/bin/env python3
"""
E-Derma Admin Panel - phpMyAdmin-like interface for SQLite
Complete database management system for E-Derma application
"""

import sqlite3
import json
import os
import csv
import shutil
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import uvicorn
from io import StringIO, BytesIO

# Database configuration
DB_PATH = "skin_analysis.db"

# Create FastAPI app
admin_app = FastAPI(title="E-Derma Admin Panel")

class DatabaseManager:
    def __init__(self, db_path):
        self.db_path = db_path
    
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def get_all_users(self):
        """Get all users with their analysis count."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT u.*, 
                   COUNT(ar.id) as analysis_count,
                   MAX(ar.created_at) as last_analysis
            FROM users u
            LEFT JOIN analysis_results ar ON u.id = ar.user_id
            GROUP BY u.id
            ORDER BY u.created_at DESC
        """)
        
        users = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return users
    
    def get_all_analyses(self):
        """Get all analysis results with user details."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT ar.*, u.name as user_name, u.contact as user_contact, u.city as user_city
            FROM analysis_results ar
            JOIN users u ON ar.user_id = u.id
            ORDER BY ar.created_at DESC
        """)
        
        analyses = []
        for row in cursor.fetchall():
            analysis = dict(row)
            # Parse JSON fields safely
            try:
                analysis['detected_issues'] = json.loads(analysis['detected_issues'])
                analysis['issue_confidence'] = json.loads(analysis['issue_confidence'])
                analysis['ingredients'] = json.loads(analysis['ingredients'])
                analysis['image_metrics'] = json.loads(analysis['image_metrics'])
            except:
                pass
            analyses.append(analysis)
        
        conn.close()
        return analyses
    
    def get_user_details(self, user_id):
        """Get detailed user information with all analyses."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Get user info
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        user = dict(cursor.fetchone()) if cursor.fetchone() else None
        
        if not user:
            return None
        
        # Get user's analyses
        cursor.execute("""
            SELECT * FROM analysis_results 
            WHERE user_id = ? 
            ORDER BY created_at DESC
        """, (user_id,))
        
        analyses = []
        for row in cursor.fetchall():
            analysis = dict(row)
            try:
                analysis['detected_issues'] = json.loads(analysis['detected_issues'])
                analysis['issue_confidence'] = json.loads(analysis['issue_confidence'])
                analysis['ingredients'] = json.loads(analysis['ingredients'])
                analysis['image_metrics'] = json.loads(analysis['image_metrics'])
            except:
                pass
            analyses.append(analysis)
        
        conn.close()
        return {'user': user, 'analyses': analyses}
    
    def get_statistics(self):
        """Get comprehensive database statistics."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        stats = {}
        
        # Basic counts
        cursor.execute("SELECT COUNT(*) as count FROM users")
        stats['total_users'] = cursor.fetchone()['count']
        
        cursor.execute("SELECT COUNT(*) as count FROM analysis_results")
        stats['total_analyses'] = cursor.fetchone()['count']
        
        cursor.execute("SELECT COUNT(*) as count FROM analysis_results WHERE gemini_used = 1")
        stats['gemini_analyses'] = cursor.fetchone()['count']
        
        # Skin type distribution
        cursor.execute("""
            SELECT skin_type, COUNT(*) as count 
            FROM analysis_results 
            GROUP BY skin_type 
            ORDER BY count DESC
        """)
        stats['skin_types'] = dict(cursor.fetchall())
        
        # Most common issues
        cursor.execute("SELECT detected_issues FROM analysis_results")
        all_issues = cursor.fetchall()
        issue_counts = {}
        for row in all_issues:
            try:
                issues = json.loads(row['detected_issues'])
                for issue in issues:
                    issue_counts[issue] = issue_counts.get(issue, 0) + 1
            except:
                pass
        stats['common_issues'] = dict(sorted(issue_counts.items(), key=lambda x: x[1], reverse=True)[:10])
        
        # Recent activity
        cursor.execute("""
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM analysis_results
            WHERE created_at >= date('now', '-7 days')
            GROUP BY DATE(created_at)
            ORDER BY date DESC
        """)
        stats['recent_activity'] = dict(cursor.fetchall())
        
        # City distribution
        cursor.execute("""
            SELECT city, COUNT(*) as count
            FROM users
            GROUP BY city
            ORDER BY count DESC
            LIMIT 10
        """)
        stats['top_cities'] = dict(cursor.fetchall())
        
        conn.close()
        return stats
    
    def delete_user(self, user_id):
        """Delete user and all associated data."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Delete analysis results first (foreign key constraint)
            cursor.execute("DELETE FROM analysis_results WHERE user_id = ?", (user_id,))
            analyses_deleted = cursor.rowcount
            
            # Delete user
            cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
            user_deleted = cursor.rowcount
            
            conn.commit()
            return {'success': True, 'analyses_deleted': analyses_deleted, 'user_deleted': user_deleted}
        except Exception as e:
            conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            conn.close()

# Initialize database manager
db_manager = DatabaseManager(DB_PATH)

@admin_app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Main dashboard - phpMyAdmin-like homepage."""
    stats = db_manager.get_statistics()
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>E-Derma Admin Panel</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f8f9fa; }}
            
            .header {{ background: linear-gradient(135deg, #22c55e, #16a34a); color: white; padding: 20px 0; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            .header-content {{ max-width: 1200px; margin: 0 auto; padding: 0 20px; display: flex; justify-content: space-between; align-items: center; }}
            .logo {{ font-size: 24px; font-weight: bold; }}
            .nav {{ display: flex; gap: 20px; }}
            .nav a {{ color: white; text-decoration: none; padding: 10px 15px; border-radius: 5px; transition: background 0.3s; }}
            .nav a:hover {{ background: rgba(255,255,255,0.2); }}
            
            .container {{ max-width: 1200px; margin: 20px auto; padding: 0 20px; }}
            
            .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 30px; }}
            .stat-card {{ background: white; padding: 25px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); text-align: center; border-left: 4px solid #22c55e; }}
            .stat-number {{ font-size: 2.5em; font-weight: bold; color: #22c55e; margin-bottom: 10px; }}
            .stat-label {{ color: #6b7280; font-size: 14px; }}
            
            .section {{ background: white; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); margin-bottom: 20px; overflow: hidden; }}
            .section-header {{ background: #f8f9fa; padding: 20px; border-bottom: 1px solid #e5e7eb; }}
            .section-title {{ font-size: 18px; font-weight: 600; color: #374151; }}
            .section-content {{ padding: 20px; }}
            
            .quick-actions {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; }}
            .action-btn {{ display: block; padding: 15px; background: #22c55e; color: white; text-decoration: none; border-radius: 8px; text-align: center; font-weight: 500; transition: background 0.3s; }}
            .action-btn:hover {{ background: #16a34a; }}
            .action-btn.secondary {{ background: #6366f1; }}
            .action-btn.secondary:hover {{ background: #4f46e5; }}
            .action-btn.danger {{ background: #ef4444; }}
            .action-btn.danger:hover {{ background: #dc2626; }}
            
            .data-table {{ width: 100%; border-collapse: collapse; }}
            .data-table th, .data-table td {{ padding: 12px; text-align: left; border-bottom: 1px solid #e5e7eb; }}
            .data-table th {{ background: #f8f9fa; font-weight: 600; color: #374151; }}
            .data-table tr:hover {{ background: #f9fafb; }}
            
            .badge {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 500; }}
            .badge.success {{ background: #dcfce7; color: #166534; }}
            .badge.info {{ background: #dbeafe; color: #1e40af; }}
            .badge.warning {{ background: #fef3c7; color: #92400e; }}
            
            .chart-container {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }}
            .chart {{ background: #f8f9fa; padding: 15px; border-radius: 8px; }}
            .chart-title {{ font-weight: 600; margin-bottom: 10px; color: #374151; }}
            .chart-item {{ display: flex; justify-content: space-between; padding: 5px 0; }}
            .chart-bar {{ height: 20px; background: #22c55e; border-radius: 10px; margin-left: 10px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <div class="header-content">
                <div class="logo">🔬 E-Derma Admin Panel</div>
                <div class="nav">
                    <a href="/">Dashboard</a>
                    <a href="/users">Users</a>
                    <a href="/analyses">Analyses</a>
                    <a href="/export">Export</a>
                </div>
            </div>
        </div>
        
        <div class="container">
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-number">{stats['total_users']}</div>
                    <div class="stat-label">Total Users</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number">{stats['total_analyses']}</div>
                    <div class="stat-label">Total Analyses</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number">{stats['gemini_analyses']}</div>
                    <div class="stat-label">Gemini AI Used</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number">{stats['total_analyses'] - stats['gemini_analyses']}</div>
                    <div class="stat-label">Heuristic Only</div>
                </div>
                
            </div>
            
            <div class="section">
                <div class="section-header">
                    <div class="section-title">Quick Actions</div>
                </div>
                <div class="section-content">
                    <div class="quick-actions">
                        <a href="/users" class="action-btn">👥 View All Users</a>
                        <a href="/analyses" class="action-btn secondary">🔬 View Analyses</a>
                        <a href="/search" class="action-btn secondary">🔍 Search Data</a>
                        <a href="/export" class="action-btn">📊 Export Data</a>
                        <a href="/backup" class="action-btn">💾 Backup Database</a>
                        <a href="/cleanup" class="action-btn danger">🗑️ Cleanup Old Data</a>
                    </div>
                </div>
            </div>
            
            <div class="chart-container">
                <div class="section">
                    <div class="section-header">
                        <div class="section-title">Top Cities</div>
                    </div>
                    <div class="section-content">
    """
    
    for city, count in list(stats['top_cities'].items())[:5]:
        percentage = (count / stats['total_users']) * 100 if stats['total_users'] > 0 else 0
        html += f"""
                        <div class="chart-item">
                            <span>{city}</span>
                            <span>{count} users ({percentage:.1f}%)</span>
                        </div>
        """
    
    html += """
                    </div>
                </div>
                
                <div class="section">
                    <div class="section-header">
                        <div class="section-title">Common Skin Issues</div>
                    </div>
                    <div class="section-content">
    """
    
    for issue, count in list(stats['common_issues'].items())[:5]:
        percentage = (count / stats['total_analyses']) * 100 if stats['total_analyses'] > 0 else 0
        html += f"""
                        <div class="chart-item">
                            <span>{issue}</span>
                            <span>{count} cases ({percentage:.1f}%)</span>
                        </div>
        """
    
    html += f"""
                    </div>
                </div>
            </div>
            
            <div class="section">
                <div class="section-header">
                    <div class="section-title">Database Information</div>
                </div>
                <div class="section-content">
                    <table class="data-table">
                        <tr>
                            <td><strong>Database File:</strong></td>
                            <td><code>{DB_PATH}</code></td>
                        </tr>
                        <tr>
                            <td><strong>Full Path:</strong></td>
                            <td><code>{os.path.abspath(DB_PATH)}</code></td>
                        </tr>
                        <tr>
                            <td><strong>File Size:</strong></td>
                            <td>{os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0} bytes</td>
                        </tr>
                        <tr>
                            <td><strong>Last Modified:</strong></td>
                            <td>{datetime.fromtimestamp(os.path.getmtime(DB_PATH)).strftime('%Y-%m-%d %H:%M:%S') if os.path.exists(DB_PATH) else 'N/A'}</td>
                        </tr>
                    </table>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    
    return html

@admin_app.get("/users", response_class=HTMLResponse)
async def users_page():
    """Users management page - like phpMyAdmin users table."""
    users = db_manager.get_all_users()
    
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Users - E-Derma Admin</title>
        <meta charset="utf-8">
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f8f9fa; }
            
            .header { background: linear-gradient(135deg, #22c55e, #16a34a); color: white; padding: 15px 0; }
            .header-content { max-width: 1200px; margin: 0 auto; padding: 0 20px; display: flex; justify-content: space-between; align-items: center; }
            .nav a { color: white; text-decoration: none; margin: 0 15px; }
            
            .container { max-width: 1200px; margin: 20px auto; padding: 0 20px; }
            .page-title { font-size: 24px; margin-bottom: 20px; color: #374151; }
            
            .controls { background: white; padding: 20px; border-radius: 10px; margin-bottom: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            .search-box { width: 300px; padding: 10px; border: 1px solid #d1d5db; border-radius: 5px; }
            .btn { padding: 10px 15px; background: #22c55e; color: white; border: none; border-radius: 5px; cursor: pointer; margin-left: 10px; }
            .btn:hover { background: #16a34a; }
            .btn.danger { background: #ef4444; }
            .btn.danger:hover { background: #dc2626; }
            
            .table-container { background: white; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow: hidden; }
            .data-table { width: 100%; border-collapse: collapse; }
            .data-table th, .data-table td { padding: 12px; text-align: left; border-bottom: 1px solid #e5e7eb; }
            .data-table th { background: #f8f9fa; font-weight: 600; color: #374151; }
            .data-table tr:hover { background: #f9fafb; }
            
            .badge { display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 500; }
            .badge.success { background: #dcfce7; color: #166534; }
            .badge.info { background: #dbeafe; color: #1e40af; }
            
            .action-links a { color: #6366f1; text-decoration: none; margin-right: 10px; }
            .action-links a:hover { text-decoration: underline; }
            .action-links a.danger { color: #ef4444; }
        </style>
    </head>
    <body>
        <div class="header">
            <div class="header-content">
                <div>🔬 E-Derma Admin Panel</div>
                <div class="nav">
                    <a href="/">Dashboard</a>
                    <a href="/users">Users</a>
                    <a href="/analyses">Analyses</a>
                </div>
            </div>
        </div>
        
        <div class="container">
            <h1 class="page-title">👥 User Management</h1>
            
            <div class="controls">
                <input type="text" class="search-box" placeholder="Search users by name, contact, or city..." id="searchBox">
                <button class="btn" onclick="searchUsers()">🔍 Search</button>
                <button class="btn" onclick="exportUsers()">📊 Export</button>
                <button class="btn" onclick="viewiuser()">📊 view</button>
            </div>
            
            <div class="table-container">
                <table class="data-table" id="usersTable">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Name</th>
                            <th>Contact</th>
                            <th>City</th>
                            <th>Skin Type</th>
                            <th>Analyses</th>
                            <th>Registered</th>
                            <th>Last Analysis</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody>
    """
    
    for user in users:
        registered = user['created_at'][:19] if user['created_at'] else 'N/A'
        last_analysis = user['last_analysis'][:19] if user['last_analysis'] else 'Never'
        analysis_badge = f'<span class="badge success">{user["analysis_count"]}</span>' if user['analysis_count'] > 0 else '<span class="badge info">0</span>'
        
        html += f"""
                        <tr>
                            <td>{user['id']}</td>
                            <td><strong>{user['name']}</strong></td>
                            <td>{user['contact']}</td>
                            <td>{user['city']}</td>
                            <td>{user['user_skin_type']}</td>
                            <td>{analysis_badge}</td>
                            <td>{registered}</td>
                            <td>{last_analysis}</td>
                            <td class="action-links">
                                <a href="/user/{user['id']}">👁️ View</a>
                                <a href="/user/{user['id']}/edit">✏️ Edit</a>
                                <a href="#" class="danger" onclick="deleteUser({user['id']}, '{user['name']}')">🗑️ Delete</a>
                            </td>
                        </tr>
        """
    
    html += f"""
                    </tbody>
                </table>
            </div>
            
            <div style="margin-top: 20px; text-align: center; color: #6b7280;">
                Total Users: {len(users)} | Database: {DB_PATH}
            </div>
        </div>
        
        <script>
            function searchUsers() {{
                const searchTerm = document.getElementById('searchBox').value.toLowerCase();
                const table = document.getElementById('usersTable');
                const rows = table.getElementsByTagName('tr');
                
                for (let i = 1; i < rows.length; i++) {{
                    const row = rows[i];
                    const text = row.textContent.toLowerCase();
                    row.style.display = text.includes(searchTerm) ? '' : 'none';
                }}
            }}
            
            function deleteUser(userId, userName) {{
                if (confirm(`Are you sure you want to delete user "${{userName}}"? This will also delete all their analysis data.`)) {{
                    fetch(`/user/${{userId}}/delete`, {{method: 'POST'}})
                    .then(response => response.json())
                    .then(data => {{
                        if (data.success) {{
                            alert('User deleted successfully');
                            location.reload();
                        }} else {{
                            alert('Error deleting user: ' + data.error);
                        }}
                    }});
                }}
            }}
            
            function exportUsers() {{
                window.open('/export/users', '_blank');
            }}
            
            // Real-time search
            document.getElementById('searchBox').addEventListener('input', searchUsers);
        </script>
    </body>
    </html>
    """
    
    return html

@admin_app.get("/user/{user_id}", response_class=HTMLResponse)
async def user_details(user_id: int):
    """Detailed user view with all analyses."""
    user_data = db_manager.get_user_details(user_id)
    
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")
    
    user = user_data['user']
    analyses = user_data['analyses']
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>User Details - {user['name']}</title>
        <meta charset="utf-8">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f8f9fa; }}
            
            .header {{ background: linear-gradient(135deg, #22c55e, #16a34a); color: white; padding: 15px 0; }}
            .header-content {{ max-width: 1200px; margin: 0 auto; padding: 0 20px; }}
            .nav a {{ color: white; text-decoration: none; margin: 0 15px; }}
            
            .container {{ max-width: 1200px; margin: 20px auto; padding: 0 20px; }}
            .back-btn {{ color: #6366f1; text-decoration: none; margin-bottom: 20px; display: inline-block; }}
            
            .user-card {{ background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); margin-bottom: 20px; }}
            .user-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }}
            .user-name {{ font-size: 28px; font-weight: bold; color: #374151; }}
            .user-info {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; }}
            .info-item {{ }}
            .info-label {{ font-weight: 600; color: #6b7280; font-size: 14px; }}
            .info-value {{ font-size: 16px; color: #374151; margin-top: 5px; }}
            
            .section {{ background: white; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); margin-bottom: 20px; }}
            .section-header {{ background: #f8f9fa; padding: 20px; border-bottom: 1px solid #e5e7eb; }}
            .section-title {{ font-size: 18px; font-weight: 600; color: #374151; }}
            .section-content {{ padding: 20px; }}
            
            .analysis-card {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 20px; margin-bottom: 15px; }}
            .analysis-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }}
            .analysis-date {{ color: #6b7280; font-size: 14px; }}
            .analysis-details {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 15px; }}
            
            .badge {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 500; }}
            .badge.success {{ background: #dcfce7; color: #166534; }}
            .badge.info {{ background: #dbeafe; color: #1e40af; }}
            .badge.warning {{ background: #fef3c7; color: #92400e; }}
            
            .json-data {{ background: #f8f9fa; padding: 10px; border-radius: 5px; font-family: monospace; font-size: 12px; max-height: 200px; overflow-y: auto; }}
        </style>
    </head>
    <body>
        <div class="header">
            <div class="header-content">
                <div>🔬 E-Derma Admin Panel</div>
                <div class="nav">
                    <a href="/">Dashboard</a>
                    <a href="/users">Users</a>
                    <a href="/analyses">Analyses</a>
                </div>
            </div>
        </div>
        
        <div class="container">
            <a href="/users" class="back-btn">← Back to Users</a>
            
            <div class="user-card">
                <div class="user-header">
                    <div class="user-name">{user['name']}</div>
                    <div>
                        <span class="badge success">{len(analyses)} Analyses</span>
                    </div>
                </div>
                
                <div class="user-info">
                    <div class="info-item">
                        <div class="info-label">Contact Number</div>
                        <div class="info-value">{user['contact']}</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">Address</div>
                        <div class="info-value">{user['address']}</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">City</div>
                        <div class="info-value">{user['city']}</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">Self-Reported Skin Type</div>
                        <div class="info-value">{user['user_skin_type']}</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">Registered</div>
                        <div class="info-value">{user['created_at'][:19] if user['created_at'] else 'N/A'}</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">User ID</div>
                        <div class="info-value">#{user['id']}</div>
                    </div>
                </div>
            </div>
            
            <div class="section">
                <div class="section-header">
                    <div class="section-title">Analysis History ({len(analyses)} records)</div>
                </div>
                <div class="section-content">
    """
    
    if analyses:
        for i, analysis in enumerate(analyses):
            ai_mode = "🤖 Gemini AI" if analysis['gemini_used'] else "🧠 Heuristic"
            severity_color = "success" if analysis['severity_score'] < 0.3 else ("warning" if analysis['severity_score'] < 0.7 else "info")
            
            html += f"""
                    <div class="analysis-card">
                        <div class="analysis-header">
                            <div>
                                <strong>Analysis #{analysis['id']}</strong>
                                <span class="badge info">{ai_mode}</span>
                            </div>
                            <div class="analysis-date">{analysis['created_at'][:19] if analysis['created_at'] else 'N/A'}</div>
                        </div>
                        
                        <div class="analysis-details">
                            <div>
                                <div class="info-label">AI-Detected Skin Type</div>
                                <div class="info-value">{analysis['skin_type']}</div>
                            </div>
                            <div>
                                <div class="info-label">Severity Score</div>
                                <div class="info-value">
                                    <span class="badge {severity_color}">{analysis['severity_score']:.2f}</span>
                                </div>
                            </div>
                            <div>
                                <div class="info-label">Analysis Mode</div>
                                <div class="info-value">{analysis['analysis_mode']}</div>
                            </div>
                        </div>
                        
                        <div style="margin-top: 15px;">
                            <div class="info-label">Detected Issues</div>
                            <div class="info-value">
            """
            
            if isinstance(analysis['detected_issues'], list):
                for issue in analysis['detected_issues']:
                    html += f'<span class="badge info" style="margin-right: 5px;">{issue}</span>'
            else:
                html += f'<span class="badge info">{analysis["detected_issues"]}</span>'
            
            html += """
                            </div>
                        </div>
                        
                        <div style="margin-top: 15px;">
                            <div class="info-label">Recommended Ingredients</div>
                            <div class="info-value">
            """
            
            if isinstance(analysis['ingredients'], list):
                for ingredient in analysis['ingredients']:
                    html += f'<span class="badge success" style="margin-right: 5px;">{ingredient}</span>'
            else:
                html += f'<span class="badge success">{analysis["ingredients"]}</span>'
            
            html += f"""
                            </div>
                        </div>
                        
                        {f'<div style="margin-top: 15px;"><div class="info-label">Gemini Note</div><div class="info-value">{analysis["gemini_note"]}</div></div>' if analysis.get('gemini_note') else ''}
                    </div>
            """
    else:
        html += '<p style="text-align: center; color: #6b7280; padding: 40px;">No analysis records found for this user.</p>'
    
    html += """
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    
    return html

@admin_app.post("/user/{user_id}/delete")
async def delete_user_endpoint(user_id: int):
    """Delete user endpoint."""
    result = db_manager.delete_user(user_id)
    return result

# Export Data Endpoints
@admin_app.get("/export")
async def export_page():
    """Export data page."""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Export Data - E-Derma Admin</title>
        <meta charset="utf-8">
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f8f9fa; }
            
            .header { background: linear-gradient(135deg, #22c55e, #16a34a); color: white; padding: 15px 0; }
            .header-content { max-width: 1200px; margin: 0 auto; padding: 0 20px; display: flex; justify-content: space-between; align-items: center; }
            .nav a { color: white; text-decoration: none; margin: 0 15px; }
            
            .container { max-width: 1200px; margin: 20px auto; padding: 0 20px; }
            .page-title { font-size: 24px; margin-bottom: 20px; color: #374151; }
            
            .export-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
            .export-card { background: white; padding: 25px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            .export-title { font-size: 18px; font-weight: 600; margin-bottom: 10px; color: #374151; }
            .export-desc { color: #6b7280; margin-bottom: 20px; font-size: 14px; }
            .export-btn { display: block; width: 100%; padding: 12px; background: #22c55e; color: white; text-decoration: none; border-radius: 8px; text-align: center; font-weight: 500; margin-bottom: 10px; }
            .export-btn:hover { background: #16a34a; }
            .export-btn.secondary { background: #6366f1; }
            .export-btn.secondary:hover { background: #4f46e5; }
        </style>
    </head>
    <body>
        <div class="header">
            <div class="header-content">
                <div>🔬 E-Derma Admin Panel</div>
                <div class="nav">
                    <a href="/">Dashboard</a>
                    <a href="/users">Users</a>
                    <a href="/analyses">Analyses</a>
                    <a href="/export">Export</a>
                </div>
            </div>
        </div>
        
        <div class="container">
            <h1 class="page-title">📊 Export Data</h1>
            
            <div class="export-grid">
                <div class="export-card">
                    <div class="export-title">👥 Users Data</div>
                    <div class="export-desc">Export all registered users with their profile information</div>
                    <a href="/export/users/csv" class="export-btn">📄 Download CSV</a>
                    <a href="/export/users/json" class="export-btn secondary">📋 Download JSON</a>
                </div>
                
                <div class="export-card">
                    <div class="export-title">🔬 Analysis Results</div>
                    <div class="export-desc">Export all skin analysis results with detailed information</div>
                    <a href="/export/analyses/csv" class="export-btn">📄 Download CSV</a>
                    <a href="/export/analyses/json" class="export-btn secondary">📋 Download JSON</a>
                </div>
                
                <div class="export-card">
                    <div class="export-title">📈 Complete Database</div>
                    <div class="export-desc">Export everything - users, analyses, and statistics</div>
                    <a href="/export/complete/json" class="export-btn">📋 Download Complete JSON</a>
                    <a href="/export/complete/excel" class="export-btn secondary">📊 Download Excel</a>
                </div>
                
                <div class="export-card">
                    <div class="export-title">📊 Statistics Report</div>
                    <div class="export-desc">Export comprehensive statistics and analytics</div>
                    <a href="/export/stats/json" class="export-btn">📋 Download Stats JSON</a>
                    <a href="/export/stats/report" class="export-btn secondary">📄 Download Report</a>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(html)

@admin_app.get("/export/users/csv")
async def export_users_csv():
    """Export users as CSV."""
    users = db_manager.get_all_users()
    
    output = StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(['ID', 'Name', 'Contact', 'Address', 'City', 'Skin Type', 'Analysis Count', 'Registered', 'Last Analysis'])
    
    # Write data
    for user in users:
        writer.writerow([
            user['id'],
            user['name'],
            user['contact'],
            user['address'],
            user['city'],
            user['user_skin_type'],
            user['analysis_count'],
            user['created_at'],
            user['last_analysis'] or 'Never'
        ])
    
    output.seek(0)
    
    return StreamingResponse(
        BytesIO(output.getvalue().encode('utf-8')),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=ederma_users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"}
    )

@admin_app.get("/export/users/json")
async def export_users_json():
    """Export users as JSON."""
    users = db_manager.get_all_users()
    
    data = {
        'export_date': datetime.now().isoformat(),
        'total_users': len(users),
        'users': users
    }
    
    json_str = json.dumps(data, indent=2, default=str)
    
    return StreamingResponse(
        BytesIO(json_str.encode('utf-8')),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=ederma_users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"}
    )

@admin_app.get("/export/analyses/csv")
async def export_analyses_csv():
    """Export analyses as CSV."""
    analyses = db_manager.get_all_analyses()
    
    output = StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(['ID', 'User Name', 'Contact', 'City', 'Skin Type', 'Issues', 'Severity', 'AI Mode', 'Gemini Used', 'Date'])
    
    # Write data
    for analysis in analyses:
        issues = ', '.join(analysis['detected_issues']) if isinstance(analysis['detected_issues'], list) else str(analysis['detected_issues'])
        writer.writerow([
            analysis['id'],
            analysis['user_name'],
            analysis['user_contact'],
            analysis['user_city'],
            analysis['skin_type'],
            issues,
            analysis['severity_score'],
            analysis['analysis_mode'],
            'Yes' if analysis['gemini_used'] else 'No',
            analysis['created_at']
        ])
    
    output.seek(0)
    
    return StreamingResponse(
        BytesIO(output.getvalue().encode('utf-8')),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=ederma_analyses_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"}
    )

@admin_app.get("/export/analyses/json")
async def export_analyses_json():
    """Export analyses as JSON."""
    analyses = db_manager.get_all_analyses()
    
    data = {
        'export_date': datetime.now().isoformat(),
        'total_analyses': len(analyses),
        'analyses': analyses
    }
    
    json_str = json.dumps(data, indent=2, default=str)
    
    return StreamingResponse(
        BytesIO(json_str.encode('utf-8')),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=ederma_analyses_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"}
    )

@admin_app.get("/export/complete/json")
async def export_complete_json():
    """Export complete database as JSON."""
    users = db_manager.get_all_users()
    analyses = db_manager.get_all_analyses()
    stats = db_manager.get_statistics()
    
    data = {
        'export_date': datetime.now().isoformat(),
        'database_info': {
            'file_path': os.path.abspath(DB_PATH),
            'file_size': os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0,
            'last_modified': datetime.fromtimestamp(os.path.getmtime(DB_PATH)).isoformat() if os.path.exists(DB_PATH) else None
        },
        'statistics': stats,
        'users': users,
        'analyses': analyses
    }
    
    json_str = json.dumps(data, indent=2, default=str)
    
    return StreamingResponse(
        BytesIO(json_str.encode('utf-8')),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=ederma_complete_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"}
    )

# Backup Database Endpoints
@admin_app.get("/backup")
async def backup_page():
    """Database backup page."""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Backup Database - E-Derma Admin</title>
        <meta charset="utf-8">
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f8f9fa; }
            
            .header { background: linear-gradient(135deg, #22c55e, #16a34a); color: white; padding: 15px 0; }
            .header-content { max-width: 1200px; margin: 0 auto; padding: 0 20px; display: flex; justify-content: space-between; align-items: center; }
            .nav a { color: white; text-decoration: none; margin: 0 15px; }
            
            .container { max-width: 800px; margin: 20px auto; padding: 0 20px; }
            .page-title { font-size: 24px; margin-bottom: 20px; color: #374151; }
            
            .backup-card { background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); margin-bottom: 20px; }
            .backup-title { font-size: 18px; font-weight: 600; margin-bottom: 15px; color: #374151; }
            .backup-desc { color: #6b7280; margin-bottom: 20px; }
            .backup-btn { display: inline-block; padding: 12px 20px; background: #22c55e; color: white; text-decoration: none; border-radius: 8px; font-weight: 500; margin-right: 10px; margin-bottom: 10px; }
            .backup-btn:hover { background: #16a34a; }
            .backup-btn.secondary { background: #6366f1; }
            .backup-btn.secondary:hover { background: #4f46e5; }
            
            .info-box { background: #f0f9ff; border: 1px solid #0ea5e9; border-radius: 8px; padding: 15px; margin-bottom: 20px; }
            .info-title { font-weight: 600; color: #0c4a6e; margin-bottom: 5px; }
            .info-text { color: #0c4a6e; font-size: 14px; }
        </style>
    </head>
    <body>
        <div class="header">
            <div class="header-content">
                <div>🔬 E-Derma Admin Panel</div>
                <div class="nav">
                    <a href="/">Dashboard</a>
                    <a href="/users">Users</a>
                    <a href="/analyses">Analyses</a>
                    <a href="/export">Export</a>
                </div>
            </div>
        </div>
        
        <div class="container">
            <h1 class="page-title">💾 Database Backup</h1>
            
            <div class="info-box">
                <div class="info-title">📋 Backup Information</div>
                <div class="info-text">
                    Regular backups ensure your E-Derma data is safe. Choose from different backup options below.
                    Backups include all user data, analysis results, and database structure.
                </div>
            </div>
            
            <div class="backup-card">
                <div class="backup-title">🗄️ Full Database Backup</div>
                <div class="backup-desc">
                    Download a complete copy of your SQLite database file. This is the most comprehensive backup option.
                </div>
                <a href="/backup/database" class="backup-btn">💾 Download Database File</a>
            </div>
            
            <div class="backup-card">
                <div class="backup-title">📊 Data Export Backup</div>
                <div class="backup-desc">
                    Export all data in JSON format. This backup can be easily imported into other systems.
                </div>
                <a href="/backup/json" class="backup-btn secondary">📋 Download JSON Backup</a>
            </div>
            
            <div class="backup-card">
                <div class="backup-title">🔄 Automated Backup</div>
                <div class="backup-desc">
                    Create a timestamped backup with database file and JSON export in a ZIP archive.
                </div>
                <a href="/backup/complete" class="backup-btn">📦 Download Complete Backup</a>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(html)

@admin_app.get("/backup/database")
async def backup_database():
    """Download database file backup."""
    if not os.path.exists(DB_PATH):
        raise HTTPException(status_code=404, detail="Database file not found")
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"ederma_backup_{timestamp}.db"
    
    return FileResponse(
        DB_PATH,
        media_type="application/octet-stream",
        filename=filename
    )

@admin_app.get("/backup/json")
async def backup_json():
    """Download JSON backup."""
    users = db_manager.get_all_users()
    analyses = db_manager.get_all_analyses()
    stats = db_manager.get_statistics()
    
    backup_data = {
        'backup_date': datetime.now().isoformat(),
        'backup_type': 'complete_json',
        'database_info': {
            'file_path': os.path.abspath(DB_PATH),
            'file_size': os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
        },
        'statistics': stats,
        'users': users,
        'analyses': analyses
    }
    
    json_str = json.dumps(backup_data, indent=2, default=str)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    return StreamingResponse(
        BytesIO(json_str.encode('utf-8')),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=ederma_backup_{timestamp}.json"}
    )

# Cleanup Old Data Endpoints
@admin_app.get("/cleanup")
async def cleanup_page():
    """Data cleanup page."""
    stats = db_manager.get_statistics()
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Cleanup Data - E-Derma Admin</title>
        <meta charset="utf-8">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f8f9fa; }}
            
            .header {{ background: linear-gradient(135deg, #22c55e, #16a34a); color: white; padding: 15px 0; }}
            .header-content {{ max-width: 1200px; margin: 0 auto; padding: 0 20px; display: flex; justify-content: space-between; align-items: center; }}
            .nav a {{ color: white; text-decoration: none; margin: 0 15px; }}
            
            .container {{ max-width: 800px; margin: 20px auto; padding: 0 20px; }}
            .page-title {{ font-size: 24px; margin-bottom: 20px; color: #374151; }}
            
            .cleanup-card {{ background: white; padding: 25px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); margin-bottom: 20px; }}
            .cleanup-title {{ font-size: 18px; font-weight: 600; margin-bottom: 10px; color: #374151; }}
            .cleanup-desc {{ color: #6b7280; margin-bottom: 15px; }}
            .cleanup-btn {{ display: inline-block; padding: 10px 15px; background: #ef4444; color: white; text-decoration: none; border-radius: 6px; font-weight: 500; cursor: pointer; border: none; }}
            .cleanup-btn:hover {{ background: #dc2626; }}
            .cleanup-btn.warning {{ background: #f59e0b; }}
            .cleanup-btn.warning:hover {{ background: #d97706; }}
            
            .warning-box {{ background: #fef3c7; border: 1px solid #f59e0b; border-radius: 8px; padding: 15px; margin-bottom: 20px; }}
            .warning-title {{ font-weight: 600; color: #92400e; margin-bottom: 5px; }}
            .warning-text {{ color: #92400e; font-size: 14px; }}
            
            .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-bottom: 20px; }}
            .stat-item {{ text-align: center; padding: 15px; background: #f8f9fa; border-radius: 8px; }}
            .stat-number {{ font-size: 24px; font-weight: bold; color: #22c55e; }}
            .stat-label {{ font-size: 12px; color: #6b7280; }}
        </style>
    </head>
    <body>
        <div class="header">
            <div class="header-content">
                <div>🔬 E-Derma Admin Panel</div>
                <div class="nav">
                    <a href="/">Dashboard</a>
                    <a href="/users">Users</a>
                    <a href="/analyses">Analyses</a>
                    <a href="/export">Export</a>
                </div>
            </div>
        </div>
        
        <div class="container">
            <h1 class="page-title">🗑️ Data Cleanup</h1>
            
            <div class="warning-box">
                <div class="warning-title">⚠️ Warning</div>
                <div class="warning-text">
                    Data cleanup operations are permanent and cannot be undone. Please create a backup before proceeding.
                </div>
            </div>
            
            <div class="stats-grid">
                <div class="stat-item">
                    <div class="stat-number">{stats['total_users']}</div>
                    <div class="stat-label">Total Users</div>
                </div>
                <div class="stat-item">
                    <div class="stat-number">{stats['total_analyses']}</div>
                    <div class="stat-label">Total Analyses</div>
                </div>
            </div>
            
            <div class="cleanup-card">
                <div class="cleanup-title">🗓️ Delete Old Analysis Data</div>
                <div class="cleanup-desc">
                    Remove analysis results older than specified time period. User profiles will be preserved.
                </div>
                <button class="cleanup-btn warning" onclick="cleanupOldAnalyses(30)">Delete 30+ days old</button>
                <button class="cleanup-btn warning" onclick="cleanupOldAnalyses(90)">Delete 90+ days old</button>
                <button class="cleanup-btn warning" onclick="cleanupOldAnalyses(365)">Delete 1+ year old</button>
            </div>
            
            <div class="cleanup-card">
                <div class="cleanup-title">👥 Delete Inactive Users</div>
                <div class="cleanup-desc">
                    Remove users who registered but never performed any skin analysis.
                </div>
                <button class="cleanup-btn" onclick="cleanupInactiveUsers()">Delete Users with 0 Analyses</button>
            </div>
            
            <div class="cleanup-card">
                <div class="cleanup-title">🧹 Database Optimization</div>
                <div class="cleanup-desc">
                    Optimize database performance by cleaning up unused space and rebuilding indexes.
                </div>
                <button class="cleanup-btn warning" onclick="optimizeDatabase()">Optimize Database</button>
            </div>
            
            <div class="cleanup-card">
                <div class="cleanup-title">💥 Complete Reset</div>
                <div class="cleanup-desc">
                    Delete ALL data and reset the database to initial state. This cannot be undone!
                </div>
                <button class="cleanup-btn" onclick="resetDatabase()">⚠️ RESET ALL DATA</button>
            </div>
        </div>
        
        <script>
            function cleanupOldAnalyses(days) {{
                if (confirm(`Are you sure you want to delete all analysis data older than ${{days}} days? This cannot be undone!`)) {{
                    fetch(`/cleanup/analyses/${{days}}`, {{method: 'POST'}})
                    .then(response => response.json())
                    .then(data => {{
                        if (data.success) {{
                            alert(`Successfully deleted ${{data.deleted_count}} old analysis records.`);
                            location.reload();
                        }} else {{
                            alert('Error: ' + data.error);
                        }}
                    }});
                }}
            }}
            
            function cleanupInactiveUsers() {{
                if (confirm('Are you sure you want to delete all users who have never performed an analysis? This cannot be undone!')) {{
                    fetch('/cleanup/inactive-users', {{method: 'POST'}})
                    .then(response => response.json())
                    .then(data => {{
                        if (data.success) {{
                            alert(`Successfully deleted ${{data.deleted_count}} inactive users.`);
                            location.reload();
                        }} else {{
                            alert('Error: ' + data.error);
                        }}
                    }});
                }}
            }}
            
            function optimizeDatabase() {{
                if (confirm('Optimize database? This may take a few moments but will improve performance.')) {{
                    fetch('/cleanup/optimize', {{method: 'POST'}})
                    .then(response => response.json())
                    .then(data => {{
                        if (data.success) {{
                            alert('Database optimized successfully!');
                        }} else {{
                            alert('Error: ' + data.error);
                        }}
                    }});
                }}
            }}
            
            function resetDatabase() {{
                const confirmation = prompt('Type "DELETE ALL DATA" to confirm complete database reset:');
                if (confirmation === 'DELETE ALL DATA') {{
                    fetch('/cleanup/reset', {{method: 'POST'}})
                    .then(response => response.json())
                    .then(data => {{
                        if (data.success) {{
                            alert('Database has been completely reset!');
                            location.reload();
                        }} else {{
                            alert('Error: ' + data.error);
                        }}
                    }});
                }} else {{
                    alert('Reset cancelled - confirmation text did not match.');
                }}
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html)

@admin_app.post("/cleanup/analyses/{days}")
async def cleanup_old_analyses(days: int):
    """Delete analysis data older than specified days."""
    try:
        conn = db_manager.get_connection()
        cursor = conn.cursor()
        
        cutoff_date = datetime.now() - timedelta(days=days)
        
        cursor.execute("""
            DELETE FROM analysis_results 
            WHERE created_at < ?
        """, (cutoff_date.isoformat(),))
        
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        return {"success": True, "deleted_count": deleted_count}
    except Exception as e:
        return {"success": False, "error": str(e)}

@admin_app.post("/cleanup/inactive-users")
async def cleanup_inactive_users():
    """Delete users with no analysis records."""
    try:
        conn = db_manager.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            DELETE FROM users 
            WHERE id NOT IN (SELECT DISTINCT user_id FROM analysis_results)
        """)
        
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        return {"success": True, "deleted_count": deleted_count}
    except Exception as e:
        return {"success": False, "error": str(e)}

@admin_app.post("/cleanup/optimize")
async def optimize_database():
    """Optimize database performance."""
    try:
        conn = db_manager.get_connection()
        cursor = conn.cursor()
        
        # Run VACUUM to reclaim space
        cursor.execute("VACUUM")
        
        # Analyze tables for better query planning
        cursor.execute("ANALYZE")
        
        conn.close()
        
        return {"success": True, "message": "Database optimized successfully"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@admin_app.post("/cleanup/reset")
async def reset_database():
    """Reset entire database - DELETE ALL DATA."""
    try:
        conn = db_manager.get_connection()
        cursor = conn.cursor()
        
        # Delete all data
        cursor.execute("DELETE FROM analysis_results")
        cursor.execute("DELETE FROM users")
        
        # Reset auto-increment counters
        cursor.execute("DELETE FROM sqlite_sequence WHERE name='users'")
        cursor.execute("DELETE FROM sqlite_sequence WHERE name='analysis_results'")
        
        conn.commit()
        conn.close()
        
        return {"success": True, "message": "Database has been completely reset"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@admin_app.get("/analyses", response_class=HTMLResponse)
async def analyses_page():
    """All analyses page."""
    analyses = db_manager.get_all_analyses()
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Analyses - E-Derma Admin</title>
        <meta charset="utf-8">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f8f9fa; }}
            
            .header {{ background: linear-gradient(135deg, #22c55e, #16a34a); color: white; padding: 15px 0; }}
            .header-content {{ max-width: 1200px; margin: 0 auto; padding: 0 20px; display: flex; justify-content: space-between; align-items: center; }}
            .nav a {{ color: white; text-decoration: none; margin: 0 15px; }}
            
            .container {{ max-width: 1200px; margin: 20px auto; padding: 0 20px; }}
            .page-title {{ font-size: 24px; margin-bottom: 20px; color: #374151; }}
            
            .controls {{ background: white; padding: 20px; border-radius: 10px; margin-bottom: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            .search-box {{ width: 300px; padding: 10px; border: 1px solid #d1d5db; border-radius: 5px; }}
            .btn {{ padding: 10px 15px; background: #22c55e; color: white; border: none; border-radius: 5px; cursor: pointer; margin-left: 10px; }}
            .btn:hover {{ background: #16a34a; }}
            
            .table-container {{ background: white; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow: hidden; }}
            .data-table {{ width: 100%; border-collapse: collapse; }}
            .data-table th, .data-table td {{ padding: 12px; text-align: left; border-bottom: 1px solid #e5e7eb; }}
            .data-table th {{ background: #f8f9fa; font-weight: 600; color: #374151; }}
            .data-table tr:hover {{ background: #f9fafb; }}
            
            .badge {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 500; }}
            .badge.success {{ background: #dcfce7; color: #166534; }}
            .badge.info {{ background: #dbeafe; color: #1e40af; }}
            .badge.warning {{ background: #fef3c7; color: #92400e; }}
            .badge.danger {{ background: #fee2e2; color: #991b1b; }}
        </style>
    </head>
    <body>
        <div class="header">
            <div class="header-content">
                <div>🔬 E-Derma Admin Panel</div>
                <div class="nav">
                    <a href="/">Dashboard</a>
                    <a href="/users">Users</a>
                    <a href="/analyses">Analyses</a>
                </div>
            </div>
        </div>
        
        <div class="container">
            <h1 class="page-title">🔬 Analysis Results</h1>
            
            <div class="controls">
                <input type="text" class="search-box" placeholder="Search by user name, skin type, or issues..." id="searchBox">
                <button class="btn" onclick="searchAnalyses()">🔍 Search</button>
                <button class="btn" onclick="exportAnalyses()">📊 Export</button>
            </div>
            
            <div class="table-container">
                <table class="data-table" id="analysesTable">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>User</th>
                            <th>Contact</th>
                            <th>City</th>
                            <th>Skin Type</th>
                            <th>Issues</th>
                            <th>Severity</th>
                            <th>AI Mode</th>
                            <th>Date</th>
                        </tr>
                    </thead>
                    <tbody>
    """
    
    for analysis in analyses:
        issues = ', '.join(analysis['detected_issues'][:2]) if isinstance(analysis['detected_issues'], list) else str(analysis['detected_issues'])[:30]
        severity_color = "success" if analysis['severity_score'] < 0.3 else ("warning" if analysis['severity_score'] < 0.7 else "danger")
        ai_mode = "🤖 Gemini" if analysis['gemini_used'] else "🧠 Heuristic"
        created_at = analysis['created_at'][:19] if analysis['created_at'] else 'N/A'
        
        html += f"""
                        <tr>
                            <td>#{analysis['id']}</td>
                            <td><strong>{analysis['user_name']}</strong></td>
                            <td>{analysis['user_contact']}</td>
                            <td>{analysis['user_city']}</td>
                            <td><span class="badge info">{analysis['skin_type']}</span></td>
                            <td>{issues}...</td>
                            <td><span class="badge {severity_color}">{analysis['severity_score']:.2f}</span></td>
                            <td><span class="badge {'success' if analysis['gemini_used'] else 'info'}">{ai_mode}</span></td>
                            <td>{created_at}</td>
                        </tr>
        """
    
    html += f"""
                    </tbody>
                </table>
            </div>
            
            <div style="margin-top: 20px; text-align: center; color: #6b7280;">
                Total Analyses: {len(analyses)} | Database: {DB_PATH}
            </div>
        </div>
        
        <script>
            function searchAnalyses() {{
                const searchTerm = document.getElementById('searchBox').value.toLowerCase();
                const table = document.getElementById('analysesTable');
                const rows = table.getElementsByTagName('tr');
                
                for (let i = 1; i < rows.length; i++) {{
                    const row = rows[i];
                    const text = row.textContent.toLowerCase();
                    row.style.display = text.includes(searchTerm) ? '' : 'none';
                }}
            }}
            
            function exportAnalyses() {{
                window.open('/export/analyses', '_blank');
            }}
            
            // Real-time search
            document.getElementById('searchBox').addEventListener('input', searchAnalyses);
        </script>
    </body>
    </html>
    """
    
    return html

if __name__ == "__main__":
    print("🚀 Starting E-Derma Admin Panel...")
    print(f"📊 Database: {os.path.abspath(DB_PATH)}")
    print("🌐 Admin Panel: http://localhost:8002")
    print("📋 Features: Users, Analyses, Search, Export")
    print("⏹️  Press Ctrl+C to stop")
    
    uvicorn.run(admin_app, host="127.0.0.1", port=8002)