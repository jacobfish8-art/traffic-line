import os
import re
import html
import requests
from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Gather

app = Flask(__name__)
GOOGLE_MAPS_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY')

def strip_html(text):
    """Remove HTML tags from Google step instructions."""
    return re.sub(r'<[^>]+>', '', text).strip()

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

def find_delay_location(leg):
    """Find the road segment with the longest travel time — likely where delay is."""
    steps = leg.get('steps', [])
    if not steps:
        return None
    longest = max(steps, key=lambda s: s['duration']['value'])
    duration_min = round(longest['duration']['value'] / 60)
    instruction = strip_html(longest.get('html_instructions', ''))
    if duration_min >= 3 and instruction:
        return f"{instruction}"
    return None

def build_message(origin_zip, dest_zip):
    routes = get_directions(origin_zip, dest_zip, avoid_tolls=False)
    routes_no_toll = get_directions(origin_zip, dest_zip, avoid_tolls=True)

    if not routes:
        return "Sorry, I could not find a route for those zip codes. Please call back and try again."

    parts = []

    # --- Best route WITH tolls ---
    best = routes[0]
    leg = best['legs'][0]
    summary = html.escape(best['summary'])
    duration = leg.get('duration_in_traffic', leg['duration'])['text']
    distance = leg['distance']['text']
    delay = calc_delay_minutes(leg)

    parts.append(f"Best route with tolls: via {summary}. Distance: {distance}. Estimated travel time with current traffic: {duration}.")

    if delay >= 5:
        delay_location = find_delay_location(leg)
        if delay_location:
            parts.append(f"There is currently a {delay} minute delay. Slowest segment: {html.escape(delay_location)}.")
        else:
            parts.append(f"There is currently a {delay} minute delay on this route.")
    else:
        parts.append("No significant delays on this route.")

    for w in best.get('warnings', []):
        parts.append(html.escape(w))

    # --- Best route WITHOUT tolls ---
    if routes_no_toll:
        nt_best = routes_no_toll[0]
        nt_leg = nt_best['legs'][0]
        nt_summary = html.escape(nt_best['summary'])
        nt_duration = nt_leg.get('duration_in_traffic', nt_leg['duration'])['text']
        nt_distance = nt_leg['distance']['text']
        nt_delay = calc_delay_minutes(nt_leg)

        if nt_summary != summary:
            parts.append(f"Best route without tolls: via {nt_summary}. Distance: {nt_distance}. Travel time: {nt_duration}.")
            if nt_delay >= 5:
                nt_delay_loc = find_delay_location(nt_leg)
                if nt_delay_loc:
                    parts.append(f"Delay of {nt_delay} minutes. Slowest segment: {html.escape(nt_delay_loc)}.")
                else:
                    parts.append(f"Delay of {nt_delay} minutes on the toll-free route.")
            else:
                parts.append("No significant delays on the toll-free route.")
        else:
            parts.append(f"The toll-free route is the same: via {nt_summary}, estimated {nt_duration}.")

    # --- All alternative routes ---
    if len(routes) > 1:
        parts.append("Additional route options:")
        for i, route in enumerate(routes[1:3], 2):
            alt_leg = route['legs'][0]
            alt_summary = html.escape(route['summary'])
            alt_duration = alt_leg.get('duration_in_traffic', alt_leg['duration'])['text']
            alt_distance = alt_leg['distance']['text']
            alt_delay = calc_delay_minutes(alt_leg)
            if alt_delay >= 5:
                alt_loc = find_delay_location(alt_leg)
                delay_str = f"{alt_delay} minute delay"
                if alt_loc:
                    delay_str += f" near {html.escape(alt_loc)}"
            else:
                delay_str = "no significant delays"
            parts.append(f"Option {i}: via {alt_summary}. {alt_distance}, {alt_duration}. {delay_str}.")

    parts.append("That is all the route information. Drive safe!")
    return " <break time='600ms'/> ".join(parts)

@app.route('/answer', methods=['POST'])
def answer():
    response = VoiceResponse()
    gather = Gather(num_digits=5, action='/origin', method='POST', timeout=10)
    gather.say(
        "<speak><prosody rate='93%'>Welcome to the traffic hotline. Please enter your 5 digit origin zip code.</prosody></speak>",
        voice='Polly.Matthew'
    )
    response.append(gather)
    return str(response)

@app.route('/origin', methods=['POST'])
def origin():
    origin_zip = request.form.get('Digits', '')
    response = VoiceResponse()
    gather = Gather(num_digits=5, action=f'/result?origin={origin_zip}', method='POST', timeout=10)
    gather.say(
        "<speak><prosody rate='93%'>Thank you. Now enter your 5 digit destination zip code.</prosody></speak>",
        voice='Polly.Matthew'
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
        f"<speak><prosody rate='93%'>{msg}</prosody></speak>",
        voice='Polly.Matthew'
    )
    return str(response)

if __name__ == '__main__':
    app.run(debug=True)
