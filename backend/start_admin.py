#!/usr/bin/env python3
"""
Auto-port finder for E-Derma Admin Panel
Automatically finds an available port and starts the admin panel
"""

import socket
import subprocess
import sys
import os

def find_free_port(start_port=8003, max_port=8020):
    """Find a free port starting from start_port."""
    for port in range(start_port, max_port):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('localhost', port))
                return port
        except OSError:
            continue
    return None

def start_admin_panel():
    """Start admin panel on available port."""
    print("🔍 Finding available port for E-Derma Admin Panel...")
    
    port = find_free_port()
    if not port:
        print("❌ No available ports found between 8003-8020")
        print("💡 Try closing other applications or restart your computer")
        return
    
    print(f"✅ Found available port: {port}")
    print(f"🌐 Admin Panel will be available at: http://localhost:{port}")
    print("⏹️  Press Ctrl+C to stop")
    print("-" * 50)
    
    # Update the port in admin_panel_simple.py temporarily
    try:
        # Start uvicorn directly with the found port
        cmd = [
            sys.executable, "-c",
            f"""
import sys
sys.path.append('{os.getcwd()}')
from admin_panel_simple import admin_app
import uvicorn
uvicorn.run(admin_app, host="0.0.0.0", port={port})
"""
        ]
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\n👋 Admin panel stopped")
    except Exception as e:
        print(f"❌ Error starting admin panel: {e}")

if __name__ == "__main__":
    start_admin_panel()