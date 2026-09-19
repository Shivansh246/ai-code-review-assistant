from flask import Flask, request
import sqlite3
import os
import pickle
import hashlib
import requests

app = Flask(__name__)

# Hardcoded credentials
# VULN: hardcoded_credentials, severity=high, lines=11-12
DB_PASS = "super_secret_db_password_123"
API_KEY = "ak_live_1234567890abcdef"

@app.route('/user')
def get_user():
    user_id = request.args.get('id')
    # VULN: sql_injection, severity=critical, lines=19-21
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
    return str(cursor.fetchall())

@app.route('/ping')
def ping_host():
    host = request.args.get('host')
    # VULN: command_injection, severity=critical, lines=28-29
    # Dangerous os.system with user input
    os.system(f"ping -c 1 {host}")
    return "Pinged"

@app.route('/load')
def load_data():
    data = request.args.get('data')
    # VULN: insecure_deserialization, severity=critical, lines=35-36
    if data:
        return pickle.loads(bytes.fromhex(data))
    return "No data"

@app.route('/read')
def read_file():
    filename = request.args.get('file')
    # VULN: path_traversal, severity=high, lines=43-44
    with open(f"/var/www/html/docs/{filename}", 'r') as f:
        return f.read()

@app.route('/hash')
def hash_password():
    password = request.args.get('pass')
    # VULN: weak_cryptography, severity=medium, lines=50-51
    # Using MD5 for passwords is weak
    return hashlib.md5(password.encode()).hexdigest()

@app.route('/fetch')
def fetch_url():
    url = request.args.get('url')
    # VULN: ssrf, severity=high, lines=57-58
    resp = requests.get(url)
    return resp.text

@app.route('/welcome')
def welcome():
    name = request.args.get('name', 'Guest')
    # VULN: xss, severity=medium, lines=64-65
    return f"<h1>Welcome {name}!</h1>"

if __name__ == '__main__':
    app.run()
