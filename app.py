import os
import re
import html
import requests
from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Gather

app = Flask(__name__)
GOOGLE_MAPS_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY')

# Known toll structures for announcements
TOLL_KEYWORDS = [
    'verrazzano', 'verrazano', 'battery tunnel', 'holland tunnel',
    'lincoln tunnel', 'george washington', 'goethals', 'outerbridge',
    'bayonne bridge', 'garden state parkway', 'new jersey turnpike',
    'nj turnpike', 'ny thruway', 'new york thruway', 'i-87 north',
    'ny-17', 'route 17'
]

def strip_html(text):
    return re.sub(r'<[^>]+>', '', text).strip()

def clean_instruction(instruction):
    """Strip navigation verbs — return just the road name"""
    prefixes = [
        r'^Continue\s+(?:straight\s+)?(?:to|on|onto|along)\s+',
        r'^Merge\s+onto\s+',
        r'^Take\s+the\s+',
        r'^Turn\s+(?:left|right|slight\s+left|slight\s+right)\s+(?:onto|toward)\s+',
        r'^Keep\s+(?:left|right)\s+(?:to\s+)?(?:continue\s+on|toward|onto)?\s*',
        r'^Use\s+the\s+.*?(?:lane|exit)\s+(?:to\s+)?(?:take|merge|turn)?\s*(?:onto|toward)?\s*',
        r'^Head\s+\w+\s+(?:on|toward)\s+',
        r'^Slight\s+(?:left|right)\s+(?:onto|toward)\s+',
        r'^Ramp\s+(?:left|right)\s+(?:onto|toward)\s+',
    ]
    cleaned = instruction
    for prefix in prefixes:
        cleaned = re.sub(prefix, '', cleaned, flags=re.IGNORECASE).strip()
    # Remove anything after slash or parenthesis
    cleaned = re.sub(r'\s*/.*$', '', cleaned).strip()
    cleaned = re.sub(r'\s*\(.*?\)\s*$', '', cleaned).strip()
    return cleaned if len(cleaned) > 3 else instruction

def get_directions(origin, destination, avoid_tolls=False):
    url = "https://maps.googleapis.com/maps/api/directions/json"
    params = {
        'origin': origin,
        'destination': destination,
        'departure_time': 'now',
        'traffic_model': 'best_guess',
        'alternatives': True,
        'key': GOOGLE_MAPS_API_KEY
    }
    if avoid_tolls:
        params['avoid'] = 'tolls'
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        print(f"API ({'no tolls' if avoid_tolls else 'with tolls'}): {data['status']}")
        if 'error_message' in data:
            print(f"Error: {data['error_message']}")
        if data['status'] != 'OK':
            return None
        return data['routes']
    except Exception as e:
        print(f"Exception: {e}")
        return None

def calc_delay_minutes(leg):
    traffic_secs = leg.get('duration_in_traffic', leg['duration'])['value']
    normal_secs = leg['duration']['value']
    return max(0, round((traffic_secs - normal_secs) / 60))

def extract_major_highways(leg):
    steps = leg.get('steps', [])
    highways = []
    seen = set()

    highway_pattern = re.compile(
        r'\b('
        # Numbered interstates and routes
        r'I-\d+[A-Z]?|Interstate\s*\d+[A-Z]?|'
        r'US-\d+|US\s*(?:Highway|Route|Hwy)?\s*\d+|'
        r'NY-\d+|NJ-\d+|CT-\d+|PA-\d+|SR-\d+|'
        r'Route\s*\d+|Rte\.?\s*\d+|'
        # Named NYC/NJ expressways
        r'FDR\s*Drive|FDR\s*Dr|'
        r'Major\s*Deegan\s*Expwy?|Major\s*Deegan\s*Expressway|'
        r'Cross\s*Bronx\s*Expwy?|Cross\s*Bronx\s*Expressway|'
        r'Staten\s*Island\s*Expwy?|Staten\s*Island\s*Expressway|'
        r'Belt\s*Pkwy?|Belt\s*Parkway|'
        r'Harlem\s*River\s*Drive|'
        r'Southern\s*State\s*Pkwy?|Northern\s*State\s*Pkwy?|'
        r'Garden\s*State\s*Pkwy?|Garden\s*State\s*Parkway|'
        r'Palisades\s*Interstate\s*Pkwy?|Palisades\s*Pkwy?|'
        r'New\s*York\s*Thruway|NY\s*Thruway|Thruway|'
        r'New\s*Jersey\s*Turnpike|NJ\s*Turnpike|Turnpike|'
        # Generic expressways/parkways
        r'(?:[\w\s]{2,20}?)\s+(?:Expressway|Expwy)|'
        r'(?:[\w\s]{2,20}?)\s+(?:Parkway|Pkwy)(?!\s+(?:Ave|Road|Street|Blvd))|'
        # Bridges and tunnels (toll indicators)
        r'(?:[\w\s]{2,25}?)\s+(?:Bridge|Tunnel|Crossing)'
        r')\b',
        re.IGNORECASE
    )

    for step in steps:
        step_distance_meters = step.get('distance', {}).get('value', 0)
        # ✅ Lower threshold to 800m to catch FDR Drive and similar
        if step_distance_meters < 800:
            continue
        instruction = strip_html(step.get('html_instructions', ''))
        matches = highway_pattern.findall(instruction)
        for m in matches:
            normalized = m.strip()
            if normalized.lower() not in seen and len(normalized) >= 4:
                seen.add(normalized.lower())
                highways.append(normalized)
    return highways

def is_toll_road(name):
    """Check if a road name is a known toll structure"""
    name_lower = name.lower()
    return any(toll in name_lower for toll in TOLL_KEYWORDS)

def find_delay_location(leg):
    steps = leg.get('steps', [])
    if not steps:
        return None

    # ✅ Method 1: Find step with highest speed drop (congestion detection)
    # Low speed on a highway = traffic jam
    # Normal highway: 60mph = 26 m/s | Heavy traffic: <15mph = <6.7 m/s
    most_congested = None
    lowest_speed = float('inf')

    for step in steps:
        distance = step.get('distance', {}).get('value', 0)
        duration = step.get('duration', {}).get('value', 1)
        if distance < 1600:  # Only check segments > 1 mile
            continue
        speed = distance / duration  # m/s
        # Flag as congested if speed < 11 m/s (~25 mph) on what should be highway
        if speed < lowest_speed:
            lowest_speed = speed
            most_congested = step

    # ✅ Method 2: Fall back to step with per-step traffic delay if available
    if not most_congested or lowest_speed > 11:
        max_delay = 0
        for step in steps:
            duration_normal = step.get('duration', {}).get('value', 0)
            duration_traffic = step.get('duration_in_traffic', {}).get('value', 0)
            distance = step.get('distance', {}).get('value', 0)
            if duration_traffic > 0 and distance > 800:
                delay = duration_traffic - duration_normal
                if delay > max_delay:
                    max_delay = delay
                    most_congested = step

    if not most_congested:
        return None

    instruction = strip_html(most_congested.get('html_instructions', ''))
    return clean_instruction(instruction)

def build_message(origin_zip, dest_zip):
    routes = get_directions(origin_zip, dest_zip, avoid_tolls=False)
    routes_no_toll = get_directions(origin_zip, dest_zip, avoid_tolls=True)

    if not routes:
        return "I'm sorry, I wasn't able to find a route for those zip codes. Please try your call again."

    parts = []
    parts.append(
        f"Alright, here's your traffic report from zip code "
        f"{' '.join(origin_zip)} to zip code {' '.join(dest_zip)}."
    )

    best = routes[0]
    leg = best['legs'][0]
    summary = html.escape(best['summary'])
    duration = leg.get('duration_in_traffic', leg['duration'])['text']
    distance = leg['distance']['text']
    delay = calc_delay_minutes(leg)

    highways = extract_major_highways(leg)

    # Check for toll roads in the highway list
    toll_roads = [h for h in highways if is_toll_road(h)]
    non_toll = [h for h in highways if not is_toll_road(h)]

    if highways:
        highway_list = ", ".join(highways)
        msg = (
            f"Your fastest route with tolls is via {summary}, "
            f"covering {distance} with a current travel time of {duration}. "
            f"This route uses the following major roads: {highway_list}."
        )
        if toll_roads:
            toll_list = ", ".join(toll_roads)
            msg += f" Note that {toll_list} {'is a' if len(toll_roads) == 1 else 'are'} toll {'road' if len(toll_roads) == 1 else 'roads'}."
        parts.append(msg)
    else:
        parts.append(
            f"Your fastest route with tolls is via {summary}, "
            f"covering {distance} with a current travel time of {duration}."
        )

    if delay >= 5:
        delay_location = find_delay_location(leg)
        if delay_location:
            parts.append(
                f"Heads up — there's about a {delay} minute delay. "
                f"The slowest stretch is along {html.escape(delay_location)}, "
                f"so expect some congestion there."
            )
        else:
            parts.append(f"Heads up — there's about a {delay} minute delay along this route.")
    else:
        parts.append("Traffic is moving well on this route with no significant delays.")

    for w in best.get('warnings', []):
        parts.append(html.escape(w))

    if routes_no_toll:
        nt_best = routes_no_toll[0]
        nt_leg = nt_best['legs'][0]
        nt_summary = html.escape(nt_best['summary'])
        nt_duration = nt_leg.get('duration_in_traffic', nt_leg['duration'])['text']
        nt_distance = nt_leg['distance']['text']
        nt_delay = calc_delay_minutes(nt_leg)
        nt_highways = extract_major_highways(nt_leg)

        if nt_summary != summary:
            if nt_highways:
                nt_highway_list = ", ".join(nt_highways)
                parts.append(
                    f"If you'd prefer to avoid tolls, your best option is via {nt_summary}, "
                    f"covering {nt_distance} in about {nt_duration}, "
                    f"using {nt_highway_list}."
                )
            else:
                parts.append(
                    f"If you'd prefer to avoid tolls, your best option is via {nt_summary}, "
                    f"covering {nt_distance} in about {nt_duration}."
                 )
            if nt_delay >= 5:
                nt_delay_loc = find_delay_location(nt_leg)
                if nt_delay_loc:
                    parts.append(
                        f"There's a {nt_delay} minute slowdown near "
                        f"{html.escape(nt_delay_loc)} on that route."
                    )
                else:
                    parts.append(f"There's about a {nt_delay} minute delay on the toll-free route.")
            else:
                parts.append("Traffic is flowing well on the toll-free route.")
        else:
            parts.append(
                f"Good news — the toll-free route follows the same road via {nt_summary}, "
                f"with an estimated travel time of {nt_duration}."
            )

    if len(routes) > 1:
        parts.append("Here are a couple of other options you might consider.")
        for i, route in enumerate(routes[1:3], 2):
            alt_leg = route['legs'][0]
            alt_summary = html.escape(route['summary'])
            alt_duration = alt_leg.get('duration_in_traffic', alt_leg['duration'])['text']
            alt_distance = alt_leg['distance']['text']
            alt_delay = calc_delay_minutes(alt_leg)
            alt_highways = extract_major_highways(alt_leg)

            if alt_delay >= 5:
                alt_loc = find_delay_location(alt_leg)
                delay_str = f"with a {alt_delay} minute delay"
                if alt_loc:
                    delay_str += f" near {html.escape(alt_loc)}"
            else:
                delay_str = "with no significant delays"

            if alt_highways:
                hw_str = ", ".join(alt_highways)
                parts.append(
                    f"Route {i}: via {alt_summary}, {alt_distance}, "
                    f"approximately {alt_duration}, {delay_str}. "
                    f"Roads used: {hw_str}."
                )
            else:
                parts.append(
                    f"Route {i}: via {alt_summary}, {alt_distance}, "
                    f"approximately {alt_duration}, {delay_str}."
                )

    # --- Branded sign-off ---
    parts.append(
        "That covers all your route options. Safe travels! "
        "Place your order by Wednesday at 3:00 PM "
        "and have it delivered to your door on Thursday. "
        "Call 7-1-8, 4-3-8, 5-7-0-7, "
        "email 15avefruit at gmail dot com, "
        "or visit 15avefruit.com."
    )

    return " <break time='600ms'/> ".join(parts)

@app.route('/answer', methods=['POST'])
def answer():
    response = VoiceResponse()
    gather = Gather(num_digits=5, action='/origin', method='POST', timeout=10)
    gather.say(
        "<speak><prosody rate='95%'>"
        "Welcome to the 15 Avenue Fruits Traffic Hotline — your Catskills travel companion! "
        "Go ahead and enter your 5 digit origin zip code."
        "</prosody></speak>",
        voice='Polly.Matthew-Neural'
    )
    response.append(gather)
    return str(response)

@app.route('/origin', methods=['POST'])
def origin():
    origin_zip = request.form.get('Digits', '')
    response = VoiceResponse()
    gather = Gather(num_digits=5, action=f'/result?origin={origin_zip}', method='POST', timeout=10)
    gather.say(
        "<speak><prosody rate='95%'>Got it. Now enter your 5 digit destination zip code.</prosody></speak>",
        voice='Polly.Matthew-Neural'
    )
    response.append(gather)
    return str(response)

@app.route('/result', methods=['POST'])
def result():
    origin_zip = request.args.get('origin', '')
    dest_zip = request.form.get('Digits', '')
    response = VoiceResponse()
    msg = build_message(origin_zip, dest_zip)
    response.say(
        f"<speak><prosody rate='95%'>{msg}</prosody></seek>",
        voice='Polly.Matthew-Neural'
    )
    return str(response)

if __name__ == '__main__':
    app.run(debug=True)
