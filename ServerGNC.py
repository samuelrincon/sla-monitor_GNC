from flask import Flask, render_template_string, request, make_response, redirect, url_for, jsonify
import requests
from bs4 import BeautifulSoup
import threading
import time
from datetime import datetime
import uuid
import os
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key')

# Global variables to store data
agent_data = {
    'alert_list': [],
    'aux_list': [],
    'chat_agents': [],
    'available_agents': [],
    'on_call_agents': [],
    'queue_data': {
        "Contacts in Queue": 0,
        "Longest waiting time": "00:00:00",
        "Callbacks in Queue": 0,
        "Total Agents": 0,
        "Last Update": datetime.now().strftime("%I:%M:%S %p")
    },
    'agent_counter_data': {
        "Total Agents": 0,
        "Available": 0,
        "Unavailable": 0,
        "Inbound": 0,
        "Outbound": 0,
        "Acw": 0,
        "Waiting": 0,
        "Preview": 0,
        "Dialer": 0,
        "Last Update": datetime.now().strftime("%I:%M:%S %p")
    },
    'kpi_values': {},
    'has_queue_calls': False,
    'token': None,
    'alert_times': {
        "Over Lunch": 60,
        "Over Break": 15,
        "Personal": 0,
        "IT Issues": 0,
        "Long Call": 7,
        "ACW": 2,
        "Unresponsible": 0,
        "Unavailable": 0
    }
}

# API endpoints
API_ENDPOINTS = {
    'agent_api_url': "https://gnc.adv-reporting.ujet.co/api/v2/dashboards/modules/currentagentstates/4aae9576-e1ab-4b9f-8c6d-ef299e489010",
    'queue_api_url': "https://gnc.adv-reporting.ujet.co/api/v2/dashboards/modules/queueCounter/2ed891eb-e620-41f2-bda5-926f5eea3cf5",
    'agent_counter_api_url': "https://gnc.adv-reporting.ujet.co/api/v2/dashboards/modules/agentCounterData/51aef3a7-0b9b-4632-8f05-e0056239b40c",
    'kpi_config_api_url': "https://gnc.adv-reporting.ujet.co/api/v2/dashboard/689cbd04-9389-4ad7-84fb-6a3ebf0d674f",
    'kpi_data_api_url': "https://gnc.adv-reporting.ujet.co/api/v2/dashboards/modules/metricreview/92ff9406-8be1-4889-9ac5-32201a39b7ae"
}

# KPI mapping
KPI_MAPPING = {
    7398: "SLA % - Call",
    7412: "AHT - Call",
    11587: "ASA - Call",
    7416: "Volume - Call",
    7396: "In SLA - Call",
    7402: "Abandoned - Call",
    11235: "Unavailable Time",
    11245: "Unresponsive Time",
    134099: "Missed Calls"
}

# Helper functions
def time_to_seconds(time_str):
    """Converts HH:MM:SS time string to seconds"""
    try:
        h, m, s = map(int, time_str.split(':'))
        return h * 3600 + m * 60 + s
    except (ValueError, AttributeError):
        return 0

def fetch_data(url, headers, params=None):
    """Generic function to fetch data from API"""
    try:
        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=15
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get('status', '').lower() != 'success':
                raise ValueError(f"API returned unsuccessful status: {data.get('message', 'Unknown error')}")
            return data
        else:
            raise requests.exceptions.HTTPError(f"HTTP Error {response.status_code}: {response.text}")
            
    except Exception as e:
        print(f"Error fetching data from {url}: {str(e)}")
        return None

def get_headers(token):
    """Returns headers with the given token"""
    return {
        "X-ACCESS-TOKEN": token,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Referer": "https://gnc.adv-reporting.ujet.co/Dashboard/DashboardNew.aspx"
    }

def token_required(f):
    """Decorator to check if token is set"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not agent_data['token']:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def update_agent_data():
    """Updates all agent data from APIs"""
    if not agent_data['token']:
        return
    
    headers = get_headers(agent_data['token'])
    
    # Fetch agent data
    agent_api_data = fetch_data(
        API_ENDPOINTS['agent_api_url'],
        headers,
        params={
            "isAutoRefresh": "false",
            "isFirstLoad": "true",
            "isCxOne": "false",
            "useMetrics": "false"
        }
    )
    
    # Clear lists
    agent_data['alert_list'] = []
    agent_data['aux_list'] = []
    agent_data['chat_agents'] = []
    agent_data['available_agents'] = []
    agent_data['on_call_agents'] = []
    
    # Process agent data
    if agent_api_data and "data" in agent_api_data and "RowValues" in agent_api_data["data"]:
        agents = agent_api_data["data"]["RowValues"]
        
        for agent in agents:
            name = agent.get("Group", {}).get("groupName", "Unknown")
            duration = agent.get("Duration", "00:00:00")
            state = agent.get("State", {}).get("DisplayState", "Unknown")
            start_time = agent.get("StartTime", "Unknown")
            
            # Convert duration to seconds
            duration_sec = time_to_seconds(duration)
            
            # Alert detection
            alert = ""
            if "Meal" in state and duration_sec > (agent_data['alert_times']["Over Lunch"] * 60):
                alert = "Over Lunch"
            elif "Break" in state and duration_sec > (agent_data['alert_times']["Over Break"] * 60):
                alert = "Over Break"
            elif "Personal" in state and duration_sec >= (agent_data['alert_times']["Personal"] * 60):
                alert = "Personal"
            elif "IT" in state and duration_sec >= (agent_data['alert_times']["IT Issues"] * 60):
                alert = "IT Issues"
            elif ("In-call" in state or "On Call" in state) and duration_sec > (agent_data['alert_times']["Long Call"] * 60):
                alert = "Long Call"
            elif "ACW" in state and duration_sec > (agent_data['alert_times']["ACW"] * 60):
                alert = "ACW"
            elif "Unresponsive" in state and duration_sec > (agent_data['alert_times']["Unresponsive"] * 60):
                alert = "Unresponsive"
            elif "Unavailable" in state and duration_sec > (agent_data['alert_times']["Unavailable"] * 60):
                alert = "Unavailable"
            
            if alert:
                agent_data['alert_list'].append((alert, name, duration, state))
            
            # Agents in AUX states
            if state not in ["Available", "On Call", "Chat", "In-call"]:
                agent_data['aux_list'].append((state, name, duration, start_time))
            
            # Separate agents in chats, available and in calls
            if "Chat" in state:
                agent_data['chat_agents'].append((name, state, duration, start_time))
            elif state == "Available":
                agent_data['available_agents'].append((name, state, duration, start_time))
            elif state == "On Call" or state == "In-call":
                agent_data['on_call_agents'].append((name, state, duration, start_time))
    
    # Fetch queue data
    queue_info = fetch_data(
        API_ENDPOINTS['queue_api_url'],
        headers,
        params={
            "isAutoRefresh": "true",
            "isFirstLoad": "true"
        }
    )
    
    if queue_info and "data" in queue_info:
        queue_info = queue_info["data"]
        agent_data['queue_data'] = {
            "Contacts in Queue": queue_info.get('BothInQueue', 0),
            "Longest waiting time": queue_info.get('LongestQueueTimeBoth', '00:00:00'),
            "Callbacks in Queue": queue_info.get('CallbacksInQueue', 0),
            "Total Agents": queue_info.get('TotalAgents', 0),
            "Last Update": datetime.now().strftime("%I:%M:%S %p")
        }
        agent_data['has_queue_calls'] = agent_data['queue_data']['Contacts in Queue'] > 0
    
    # Fetch agent counter data
    agent_counter_info = fetch_data(
        API_ENDPOINTS['agent_counter_api_url'],
        headers,
        params={
            "isAutoRefresh": "false",
            "isFirstLoad": "true"
        }
    )
    
    if agent_counter_info and "data" in agent_counter_info:
        agent_data_info = agent_counter_info["data"]
        agent_data['agent_counter_data'] = {
            "Total Agents": agent_data_info.get('Total', 0),
            "Available": agent_data_info.get('Available', 0),
            "Unavailable": agent_data_info.get('Unavailable', 0),
            "Inbound": agent_data_info.get('Inbound', 0),
            "Outbound": agent_data_info.get('Outbound', 0),
            "Acw": agent_data_info.get('Acw', 0),
            "Waiting": agent_data_info.get('Waiting', 0),
            "Preview": agent_data_info.get('Preview', 0),
            "Dialer": agent_data_info.get('Dialer', 0),
            "Last Update": datetime.now().strftime("%I:%M:%S %p")
        }
    
    # Fetch KPI data
    kpi_data = fetch_data(
        API_ENDPOINTS['kpi_data_api_url'],
        headers,
        params={
            "isAutoRefresh": "true",
            "isFirstLoad": "false"
        }
    )
    
    if kpi_data and "data" in kpi_data:
        agent_data['kpi_values'] = {}
        for metric in kpi_data["data"].get("Metrics", []):
            metric_id = metric.get("Metric", {}).get("MetricID")
            metric_value = metric.get("Today", {}).get("MetricValue")
            metric_display = metric.get("Today", {}).get("MetricDisplayValue")
            
            if metric_id in KPI_MAPPING:
                agent_data['kpi_values'][metric_id] = {
                    "name": KPI_MAPPING[metric_id],
                    "value": metric_value,
                    "display": metric_display
                }

def background_updater():
    """Background thread to update data periodically"""
    while True:
        update_agent_data()
        time.sleep(10)  # Update every 10 seconds

# Start background updater thread
updater_thread = threading.Thread(target=background_updater)
updater_thread.daemon = True
updater_thread.start()

# Routes
@app.route('/')
def login():
    """Login page to enter access token"""
    if agent_data['token']:
        return redirect(url_for('dashboard'))
    
    return render_template_string('''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Token Authentication</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    background-color: #F6F0FF;
                    margin: 0;
                    padding: 0;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                }
                .login-container {
                    background-color: white;
                    border-radius: 10px;
                    box-shadow: 0 0 20px rgba(0, 0, 0, 0.1);
                    width: 400px;
                    padding: 30px;
                    text-align: center;
                }
                .logo {
                    background-color: #6A0DAD;
                    color: white;
                    padding: 15px;
                    border-radius: 15px;
                    font-size: 24px;
                    font-weight: bold;
                    margin-bottom: 20px;
                    display: inline-block;
                }
                h1 {
                    color: #6A0DAD;
                    margin-bottom: 30px;
                }
                .form-group {
                    margin-bottom: 20px;
                    text-align: left;
                }
                label {
                    display: block;
                    margin-bottom: 5px;
                    font-weight: bold;
                    color: #555;
                }
                input[type="password"] {
                    width: 100%;
                    padding: 10px;
                    border: 2px solid #ddd;
                    border-radius: 5px;
                    font-size: 16px;
                    box-sizing: border-box;
                }
                .btn {
                    background-color: #6A0DAD;
                    color: white;
                    border: none;
                    padding: 10px 20px;
                    font-size: 16px;
                    border-radius: 5px;
                    cursor: pointer;
                    font-weight: bold;
                    margin: 5px;
                }
                .btn:hover {
                    background-color: #5a0b9d;
                }
                .btn-exit {
                    background-color: #D32F2F;
                }
                .btn-exit:hover {
                    background-color: #b71c1c;
                }
                .message {
                    margin-top: 20px;
                    padding: 10px;
                    border-radius: 5px;
                    font-size: 14px;
                }
                .error {
                    color: red;
                }
                .success {
                    color: green;
                }
            </style>
        </head>
        <body>
            <div class="login-container">
                <div class="logo">GNC/Ujet</div>
                <h1>Token Authentication</h1>
                <form method="POST" action="/verify_token">
                    <div class="form-group">
                        <label for="token">Enter Access Token:</label>
                        <input type="password" id="token" name="token" required>
                    </div>
                    {% if message %}
                    <div class="message {{ message_type }}">{{ message }}</div>
                    {% endif %}
                    <div>
                        <button type="submit" class="btn">Verify Token</button>
                        <button type="button" class="btn btn-exit" onclick="window.close()">Exit</button>
                    </div>
                </form>
            </div>
        </body>
        </html>
    ''', message=request.args.get('message'), message_type=request.args.get('message_type'))

@app.route('/verify_token', methods=['POST'])
def verify_token():
    """Verify the provided token"""
    token = request.form.get('token', '').strip()
    
    if not token:
        return redirect(url_for('login', message='Please enter a token', message_type='error'))
    
    try:
        headers = get_headers(token)
        response = requests.get(
            API_ENDPOINTS['agent_api_url'],
            headers=headers,
            params={
                "isAutoRefresh": "false",
                "isFirstLoad": "true",
                "isCxOne": "false",
                "useMetrics": "false"
            },
            timeout=15
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get('status', '').lower() == 'success' and 'data' in data and 'RowValues' in data['data']:
                agent_data['token'] = token
                return redirect(url_for('dashboard'))
            else:
                error_msg = data.get('message', 'Invalid token response. Please try again.')
                return redirect(url_for('login', message=f"Error: {error_msg}", message_type='error'))
        else:
            error_msg = f"Server returned status code {response.status_code}"
            try:
                error_data = response.json()
                error_msg = error_data.get('message', error_msg)
            except ValueError:
                pass
            return redirect(url_for('login', message=f"Error: {error_msg}", message_type='error'))
            
    except requests.exceptions.RequestException as e:
        return redirect(url_for('login', message=f"Connection error: {str(e)}", message_type='error'))
    except Exception as e:
        return redirect(url_for('login', message=f"An unexpected error occurred: {str(e)}", message_type='error'))

@app.route('/dashboard')
@token_required
def dashboard():
    """Main dashboard page"""
    return render_template_string('''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Agent Monitor - IntouchCX</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    background-color: #F6F0FF;
                    margin: 0;
                    padding: 0;
                }
                .header {
                    background-color: white;
                    padding: 20px;
                    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                }
                .logo {
                    background-color: #6A0DAD;
                    color: white;
                    padding: 10px 20px;
                    border-radius: 15px;
                    font-size: 20px;
                    font-weight: bold;
                    display: inline-block;
                }
                .company-name {
                    color: #6A0DAD;
                    font-size: 35px;
                    font-weight: bold;
                }
                .notification {
                    background-color: red;
                    color: white;
                    padding: 10px;
                    text-align: center;
                    font-weight: bold;
                    margin: 10px;
                    border-radius: 5px;
                    display: {% if has_queue_calls %}block{% else %}none{% endif %};
                }
                .dashboard-container {
                    display: flex;
                    padding: 20px;
                    gap: 20px;
                }
                .panel {
                    flex: 1;
                    background-color: white;
                    border-radius: 10px;
                    box-shadow: 0 0 10px rgba(0, 0, 0, 0.1);
                    padding: 15px;
                }
                .panel-title {
                    background-color: #6A0DAD;
                    color: white;
                    padding: 10px;
                    border-radius: 5px;
                    font-weight: bold;
                    margin-bottom: 15px;
                    text-align: center;
                }
                table {
                    width: 100%;
                    border-collapse: collapse;
                }
                th, td {
                    padding: 10px;
                    text-align: left;
                    border-bottom: 1px solid #ddd;
                }
                th {
                    background-color: #f2f2f2;
                }
                tr:nth-child(even) {
                    background-color: #DAC8FF;
                }
                tr:nth-child(odd) {
                    background-color: #D0B5FF;
                }
                .button-container {
                    display: flex;
                    justify-content: center;
                    gap: 10px;
                    margin-top: 20px;
                    flex-wrap: wrap;
                }
                .btn {
                    background-color: #6A0DAD;
                    color: white;
                    border: none;
                    padding: 10px 20px;
                    font-size: 16px;
                    border-radius: 5px;
                    cursor: pointer;
                    font-weight: bold;
                    text-decoration: none;
                    display: inline-block;
                }
                .btn:hover {
                    background-color: #5a0b9d;
                }
                .change-token {
                    position: fixed;
                    bottom: 20px;
                    right: 20px;
                    background-color: #6A0DAD;
                    color: white;
                    padding: 10px 15px;
                    border-radius: 5px;
                    text-decoration: none;
                    font-weight: bold;
                }
                .change-token:hover {
                    background-color: #5a0b9d;
                }
            </style>
        </head>
        <body>
            <div class="header">
                <div class="company-name">IntouchCX</div>
                <div class="logo">GNC/Ujet</div>
            </div>
            
            <div class="notification" id="notification">
                ⚠️ Contacts in Queue: {{ queue_data['Contacts in Queue'] }} | 
                Longest Wait: {{ queue_data['Longest waiting time'] }} | 
                Callbacks: {{ queue_data['Callbacks in Queue'] }} ⚠️
            </div>
            
            <div class="dashboard-container">
                <div class="panel">
                    <div class="panel-title">Agents in Chat</div>
                    <table>
                        <thead>
                            <tr>
                                <th>Agent Name</th>
                                <th>State</th>
                                <th>Duration</th>
                                <th>Since</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for agent in chat_agents %}
                            <tr>
                                <td>{{ agent[0] }}</td>
                                <td>{{ agent[1] }}</td>
                                <td>{{ agent[2] }}</td>
                                <td>{{ agent[3] }}</td>
                            </tr>
                            {% else %}
                            <tr>
                                <td colspan="4" style="text-align: center;">No agents in chat</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
                
                <div class="panel">
                    <div class="panel-title">Available/In-Call Agents</div>
                    <table>
                        <thead>
                            <tr>
                                <th>Agent Name</th>
                                <th>State</th>
                                <th>Duration</th>
                                <th>Since</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for agent in available_agents %}
                            <tr>
                                <td>{{ agent[0] }}</td>
                                <td>{{ agent[1] }}</td>
                                <td>{{ agent[2] }}</td>
                                <td>{{ agent[3] }}</td>
                            </tr>
                            {% else %}
                            <tr>
                                <td colspan="4" style="text-align: center;">No available agents</td>
                            </tr>
                            {% endfor %}
                            {% for agent in on_call_agents %}
                            <tr>
                                <td>{{ agent[0] }}</td>
                                <td>{{ agent[1] }}</td>
                                <td>{{ agent[2] }}</td>
                                <td>{{ agent[3] }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
            
            <div class="button-container">
                <a href="/alerts" class="btn">View Alerts</a>
                <a href="/aux" class="btn">View AUX Status</a>
                <a href="/queue" class="btn">View Queue</a>
                <a href="/agent_states" class="btn">Agent States</a>
                <a href="/kpis" class="btn">View KPIs</a>
                <a href="/settings" class="btn">Settings</a>
            </div>
            
            <a href="/change_token" class="change-token">Change Token</a>
            
            <script>
                // Auto-refresh every 10 seconds
                setTimeout(function() {
                    window.location.reload();
                }, 10000);
            </script>
        </body>
        </html>
    ''', 
    chat_agents=agent_data['chat_agents'],
    available_agents=agent_data['available_agents'],
    on_call_agents=agent_data['on_call_agents'],
    queue_data=agent_data['queue_data'],
    has_queue_calls=agent_data['has_queue_calls'])

@app.route('/alerts')
@token_required
def alerts():
    """Active alerts page"""
    return render_template_string('''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <meta http-equiv="refresh" content="15">
            <title>Active Alerts</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    background-color: #FFB6C1;
                    margin: 0;
                    padding: 0;
                }
                .container {
                    max-width: 800px;
                    margin: 0 auto;
                    padding: 20px;
                    background-color: white;
                    border-radius: 10px;
                    box-shadow: 0 0 20px rgba(0, 0, 0, 0.1);
                    margin-top: 20px;
                    margin-bottom: 20px;
                }
                h1 {
                    text-align: center;
                    color: black;
                    margin-bottom: 20px;
                }
                .alert-section {
                    margin-bottom: 20px;
                }
                .alert-title {
                    font-weight: bold;
                    font-size: 18px;
                    color: red;
                    margin-bottom: 10px;
                }
                .alert-item {
                    margin-left: 20px;
                    margin-bottom: 5px;
                }
                .no-alerts {
                    text-align: center;
                    color: gray;
                    font-style: italic;
                    padding: 20px;
                }
                .btn {
                    display: block;
                    width: 150px;
                    margin: 20px auto;
                    background-color: red;
                    color: white;
                    border: none;
                    padding: 10px;
                    font-size: 16px;
                    border-radius: 5px;
                    cursor: pointer;
                    font-weight: bold;
                    text-align: center;
                    text-decoration: none;
                }
                .btn:hover {
                    background-color: #d32f2f;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>⚠️ ACTIVE ALERTS ⚠️</h1>
                
                {% if alert_list %}
                    {% set alerts_dict = {} %}
                    {% for alert in alert_list %}
                        {% if alert[0] not in alerts_dict %}
                            {% set _ = alerts_dict.update({alert[0]: []}) %}
                        {% endif %}
                        {% set _ = alerts_dict[alert[0]].append(alert[1] + " - " + alert[3] + " (" + alert[2] + ")") %}
                    {% endfor %}
                    
                    {% for alert_type, agents in alerts_dict.items() %}
                        <div class="alert-section">
                            <div class="alert-title">{{ alert_type.upper() }}</div>
                            {% for agent in agents %}
                                <div class="alert-item">{{ agent }}</div>
                            {% endfor %}
                        </div>
                    {% endfor %}
                {% else %}
                    <div class="no-alerts">No active alerts at the moment.</div>
                {% endif %}
                
                <a href="/dashboard" class="btn">Close</a>
            </div>
        </body>
        </html>
    ''', alert_list=agent_data['alert_list'])

@app.route('/aux')
@token_required
def aux_status():
    """AUX/Special states page"""
    return render_template_string('''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>AUX/Special States</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    background-color: #FFB6C1;
                    margin: 0;
                    padding: 0;
                }
                .container {
                    max-width: 800px;
                    margin: 0 auto;
                    padding: 20px;
                    background-color: white;
                    border-radius: 10px;
                    box-shadow: 0 0 20px rgba(0, 0, 0, 0.1);
                    margin-top: 20px;
                    margin-bottom: 20px;
                }
                h1 {
                    text-align: center;
                    color: black;
                    margin-bottom: 20px;
                }
                .state-section {
                    margin-bottom: 20px;
                }
                .state-title {
                    font-weight: bold;
                    font-size: 18px;
                    color: purple;
                    margin-bottom: 10px;
                }
                .state-item {
                    margin-left: 20px;
                    margin-bottom: 5px;
                }
                .no-states {
                    text-align: center;
                    color: gray;
                    font-style: italic;
                    padding: 20px;
                }
                .btn {
                    display: block;
                    width: 150px;
                    margin: 20px auto;
                    background-color: red;
                    color: white;
                    border: none;
                    padding: 10px;
                    font-size: 16px;
                    border-radius: 5px;
                    cursor: pointer;
                    font-weight: bold;
                    text-align: center;
                    text-decoration: none;
                }
                .btn:hover {
                    background-color: #d32f2f;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>AUX/Special States</h1>
                
                {% if aux_list %}
                    {% set states_dict = {} %}
                    {% for state in aux_list %}
                        {% if state[0] not in states_dict %}
                            {% set _ = states_dict.update({state[0]: []}) %}
                        {% endif %}
                        {% set _ = states_dict[state[0]].append(state[1] + " - " + state[2] + " (since " + state[3] + ")") %}
                    {% endfor %}
                    
                    {% for state, agents in states_dict.items() %}
                        <div class="state-section">
                            <div class="state-title">{{ state }}</div>
                            {% for agent in agents %}
                                <div class="state-item">{{ agent }}</div>
                            {% endfor %}
                        </div>
                    {% endfor %}
                {% else %}
                    <div class="no-states">No agents in AUX or special states.</div>
                {% endif %}
                
                <a href="/dashboard" class="btn">Close</a>
            </div>
        </body>
        </html>
    ''', aux_list=agent_data['aux_list'])

@app.route('/queue')
@token_required
def queue_status():
    """Queue status page"""
    return render_template_string('''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Queue Status</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    background-color: #E8F5E9;
                    margin: 0;
                    padding: 0;
                }
                .container {
                    max-width: 600px;
                    margin: 0 auto;
                    padding: 20px;
                    background-color: white;
                    border-radius: 10px;
                    box-shadow: 0 0 20px rgba(0, 0, 0, 0.1);
                    margin-top: 20px;
                    margin-bottom: 20px;
                }
                h1 {
                    text-align: center;
                    color: #2E7D32;
                    margin-bottom: 20px;
                }
                .data-row {
                    display: flex;
                    justify-content: space-between;
                    margin-bottom: 10px;
                    padding: 10px;
                    border-bottom: 1px solid #eee;
                }
                .label {
                    font-weight: normal;
                    color: #555;
                }
                .value {
                    font-weight: bold;
                    color: #2E7D32;
                }
                .update-time {
                    text-align: right;
                    color: #555;
                    font-size: 12px;
                    margin-top: 20px;
                }
                .btn {
                    display: block;
                    width: 150px;
                    margin: 20px auto;
                    background-color: #2E7D32;
                    color: white;
                    border: none;
                    padding: 10px;
                    font-size: 16px;
                    border-radius: 5px;
                    cursor: pointer;
                    font-weight: bold;
                    text-align: center;
                    text-decoration: none;
                }
                .btn:hover {
                    background-color: #1B5E20;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>QUEUE STATUS</h1>
                
                <div class="data-row">
                    <span class="label">Contacts in Queue:</span>
                    <span class="value">{{ queue_data['Contacts in Queue'] }}</span>
                </div>
                
                <div class="data-row">
                    <span class="label">Longest Waiting Time:</span>
                    <span class="value">{{ queue_data['Longest waiting time'] }}</span>
                </div>
                
                <div class="data-row">
                    <span class="label">Callbacks in Queue:</span>
                    <span class="value">{{ queue_data['Callbacks in Queue'] }}</span>
                </div>
                
                <div class="data-row">
                    <span class="label">Total Agents:</span>
                    <span class="value">{{ queue_data['Total Agents'] }}</span>
                </div>
                
                <div class="update-time">
                    Last update: {{ queue_data['Last Update'] }}
                </div>
                
                <a href="/dashboard" class="btn">Close</a>
            </div>
        </body>
        </html>
    ''', queue_data=agent_data['queue_data'])

@app.route('/agent_states')
@token_required
def agent_states():
    """Agent states summary page"""
    return render_template_string('''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Agent States Summary</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    background-color: #E3F2FD;
                    margin: 0;
                    padding: 0;
                }
                .container {
                    max-width: 600px;
                    margin: 0 auto;
                    padding: 20px;
                    background-color: white;
                    border-radius: 10px;
                    box-shadow: 0 0 20px rgba(0, 0, 0, 0.1);
                    margin-top: 20px;
                    margin-bottom: 20px;
                }
                h1 {
                    text-align: center;
                    color: #0D47A1;
                    margin-bottom: 20px;
                }
                .data-row {
                    display: flex;
                    justify-content: space-between;
                    margin-bottom: 10px;
                    padding: 10px;
                    border-bottom: 1px solid #eee;
                }
                .label {
                    font-weight: normal;
                    color: #555;
                }
                .value {
                    font-weight: bold;
                    color: #0D47A1;
                }
                .update-time {
                    text-align: right;
                    color: #555;
                    font-size: 12px;
                    margin-top: 20px;
                }
                .btn {
                    display: block;
                    width: 150px;
                    margin: 20px auto;
                    background-color: #0D47A1;
                    color: white;
                    border: none;
                    padding: 10px;
                    font-size: 16px;
                    border-radius: 5px;
                    cursor: pointer;
                    font-weight: bold;
                    text-align: center;
                    text-decoration: none;
                }
                .btn:hover {
                    background-color: #0D2C7D;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>AGENT STATES SUMMARY</h1>
                
                <div class="data-row">
                    <span class="label">Total Agents:</span>
                    <span class="value">{{ agent_counter_data['Total Agents'] }}</span>
                </div>
                
                <div class="data-row">
                    <span class="label">Available:</span>
                    <span class="value">{{ agent_counter_data['Available'] }}</span>
                </div>
                
                <div class="data-row">
                    <span class="label">Unavailable:</span>
                    <span class="value">{{ agent_counter_data['Unavailable'] }}</span>
                </div>
                
                <div class="data-row">
                    <span class="label">Inbound:</span>
                    <span class="value">{{ agent_counter_data['Inbound'] }}</span>
                </div>
                
                <div class="data-row">
                    <span class="label">Outbound:</span>
                    <span class="value">{{ agent_counter_data['Outbound'] }}</span>
                </div>
                
                <div class="data-row">
                    <span class="label">After Call Work:</span>
                    <span class="value">{{ agent_counter_data['Acw'] }}</span>
                </div>
                
                <div class="data-row">
                    <span class="label">Waiting:</span>
                    <span class="value">{{ agent_counter_data['Waiting'] }}</span>
                </div>
                
                <div class="data-row">
                    <span class="label">Preview:</span>
                    <span class="value">{{ agent_counter_data['Preview'] }}</span>
                </div>
                
                <div class="data-row">
                    <span class="label">Dialer:</span>
                    <span class="value">{{ agent_counter_data['Dialer'] }}</span>
                </div>
                
                <div class="update-time">
                    Last update: {{ agent_counter_data['Last Update'] }}
                </div>
                
                <a href="/dashboard" class="btn">Close</a>
            </div>
        </body>
        </html>
    ''', agent_counter_data=agent_data['agent_counter_data'])

@app.route('/kpis')
@token_required
def kpis():
    """Key Performance Indicators page"""
    return render_template_string('''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Key Performance Indicators</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    background-color: #E8F5E9;
                    margin: 0;
                    padding: 0;
                }
                .container {
                    max-width: 600px;
                    margin: 0 auto;
                    padding: 20px;
                    background-color: white;
                    border-radius: 10px;
                    box-shadow: 0 0 20px rgba(0, 0, 0, 0.1);
                    margin-top: 20px;
                    margin-bottom: 20px;
                }
                h1 {
                    text-align: center;
                    color: #2E7D32;
                    margin-bottom: 20px;
                }
                .kpi-item {
                    border: 1px solid #ddd;
                    border-radius: 5px;
                    padding: 15px;
                    margin-bottom: 15px;
                }
                .kpi-name {
                    font-weight: bold;
                    font-size: 16px;
                    color: #333;
                    margin-bottom: 5px;
                }
                .kpi-value {
                    font-size: 20px;
                    color: #2E7D32;
                    text-align: right;
                }
                .button-container {
                    display: flex;
                    justify-content: center;
                    gap: 10px;
                    margin-top: 20px;
                }
                .btn {
                    background-color: #2E7D32;
                    color: white;
                    border: none;
                    padding: 10px 20px;
                    font-size: 16px;
                    border-radius: 5px;
                    cursor: pointer;
                    font-weight: bold;
                    text-decoration: none;
                }
                .btn:hover {
                    background-color: #1B5E20;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>KEY PERFORMANCE INDICATORS</h1>
                
                {% if kpi_values %}
                    {% for metric_id, kpi in kpi_values.items() %}
                        <div class="kpi-item">
                            <div class="kpi-name">{{ kpi['name'] }}</div>
                            <div class="kpi-value">{{ kpi['display'] }}</div>
                        </div>
                    {% endfor %}
                {% else %}
                    <div style="text-align: center; color: gray; font-style: italic; padding: 20px;">
                        No KPI data available
                    </div>
                {% endif %}
                
                <div class="button-container">
                    <a href="/dashboard" class="btn">Close</a>
                    <a href="/kpis" class="btn">Refresh</a>
                </div>
            </div>
        </body>
        </html>
    ''', kpi_values=agent_data['kpi_values'])

@app.route('/settings', methods=['GET', 'POST'])
@token_required
def settings():
    """Alert settings page"""
    if request.method == 'POST':
        if 'apply' in request.form:
            try:
                for alert in agent_data['alert_times']:
                    agent_data['alert_times'][alert] = int(request.form.get(alert, 0))
                return redirect(url_for('settings', message='Custom times applied successfully!', message_type='success'))
            except ValueError:
                return redirect(url_for('settings', message='Please enter valid numbers for all fields.', message_type='error'))
        elif 'default' in request.form:
            agent_data['alert_times'] = {
                "Over Lunch": 60,
                "Over Break": 15,
                "Personal": 0,
                "IT Issues": 0,
                "Long Call": 7,
                "ACW": 2,
                "Unresponsible": 0,
                "Unavailable": 0
            }
            return redirect(url_for('settings', message='Default times restored successfully!', message_type='success'))
    
    return render_template_string('''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Alert Settings</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    background-color: #F6F0FF;
                    margin: 0;
                    padding: 0;
                }
                .container {
                    max-width: 500px;
                    margin: 0 auto;
                    padding: 20px;
                    background-color: white;
                    border-radius: 10px;
                    box-shadow: 0 0 20px rgba(0, 0, 0, 0.1);
                    margin-top: 20px;
                    margin-bottom: 20px;
                }
                h1 {
                    text-align: center;
                    color: black;
                    margin-bottom: 20px;
                }
                .form-group {
                    margin-bottom: 15px;
                }
                label {
                    display: block;
                    margin-bottom: 5px;
                    font-weight: bold;
                }
                input {
                    width: 100%;
                    padding: 8px;
                    border: 1px solid #ddd;
                    border-radius: 4px;
                    box-sizing: border-box;
                }
                .button-container {
                    display: flex;
                    justify-content: space-between;
                    margin-top: 20px;
                }
                .btn {
                    background-color: #6A0DAD;
                    color: white;
                    border: none;
                    padding: 10px 20px;
                    font-size: 16px;
                    border-radius: 5px;
                    cursor: pointer;
                    font-weight: bold;
                }
                .btn:hover {
                    background-color: #5a0b9d;
                }
                .message {
                    margin-bottom: 20px;
                    padding: 10px;
                    border-radius: 5px;
                    text-align: center;
                }
                .error {
                    background-color: #ffebee;
                    color: #d32f2f;
                }
                .success {
                    background-color: #e8f5e9;
                    color: #2e7d32;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Alert Settings</h1>
                
                {% if message %}
                <div class="message {{ message_type }}">{{ message }}</div>
                {% endif %}
                
                <form method="POST">
                    {% for alert, time in alert_times.items() %}
                    <div class="form-group">
                        <label for="{{ alert }}">{{ alert }} (minutes):</label>
                        <input type="number" id="{{ alert }}" name="{{ alert }}" value="{{ time }}" min="0">
                    </div>
                    {% endfor %}
                    
                    <div class="button-container">
                        <button type="submit" name="apply" class="btn">Apply</button>
                        <button type="submit" name="default" class="btn">Use Default Times</button>
                    </div>
                </form>
            </div>
        </body>
        </html>
    ''', alert_times=agent_data['alert_times'], 
    message=request.args.get('message'), 
    message_type=request.args.get('message_type', 'error'))

@app.route('/change_token')
def change_token():
    """Change token route"""
    agent_data['token'] = None
    return redirect(url_for('login'))

@app.route('/api/data')
@token_required
def api_data():
    """API endpoint to get current data (for potential future AJAX updates)"""
    return jsonify({
        'queue_data': agent_data['queue_data'],
        'agent_counter_data': agent_data['agent_counter_data'],
        'has_queue_calls': agent_data['has_queue_calls'],
        'alert_count': len(agent_data['alert_list']),
        'last_update': datetime.now().strftime("%I:%M:%S %p")
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))