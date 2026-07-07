import os
import re
import html
import requests
from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Gather

app = Flask(__name__)
GOOGLE_MAPS_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY')

def strip_html(text):
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
    steps = leg.get('steps', [])
    if not steps:
        return None
    longest = max(steps, key=lambda s: s['duration']['value'])
    duration_min = round(longest['duration']['value'] / 60)
    instruction = strip_html(longest.get('html_instructions', ''))
    if duration_min >= 3 and instruction:
        return instruction
    return None

def build_message(origin_zip, dest_zip):
    routes = get_directions(origin_zip, dest_zip, avoid_tolls=False)
    routes_no_toll = get_directions(origin_zip, dest_zip, avoid_tolls=True)

    if not routes:
        return "I'm sorry, I wasn't able to find a route for those zip codes. Please try your call again."

    parts = []
    parts.append(f"Alright, here's your traffic report from zip code {' '.join(origin_zip)} to zip code {' '.join(dest_zip)}.")

    # --- Best route WITH tolls ---
    best = routes[0]
    leg = best['legs'][0]
    summary = html.escape(best['summary'])
    duration = leg.get('duration_in_traffic', leg['duration'])['text']
    distance = leg['distance']['text']
    delay = calc_delay_minutes(leg)

    parts.append(f"Your fastest route with tolls is via {summary}, covering {distance} with a current travel time of {duration}.")

    if delay >= 5:
        delay_location = find_delay_location(leg)
        if delay_location:
            parts.append(f"Heads up — there's about a {delay} minute delay. The slowest stretch is along {html.escape(delay_location)}, so expect some congestion there.")
        else:
            parts.append(f"Heads up — there's about a {delay} minute delay along this route.")
    else:
        parts.append("Traffic is moving well on this route with no significant delays.")

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
            parts.append(f"If you'd prefer to avoid tolls, your best option is via {nt_summary}, covering {nt_distance} in about {nt_duration}.")
            if nt_delay >= 5:
                nt_delay_loc = find_delay_location(nt_leg)
                if nt_delay_loc:
                    parts.append(f"There's a {nt_delay} minute slowdown near {html.escape(nt_delay_loc)} on that route.")
                else:
                    parts.append(f"There's about a {nt_delay} minute delay on the toll-free route.")
            else:
                parts.append("Traffic is flowing well on the toll-free route.")
        else:
            parts.append(f"Good news — the toll-free route follows the same road via {nt_summary}, with an estimated travel time of {nt_duration}.")

    # --- Alternative routes ---
    if len(routes) > 1:
        parts.append("Here are a couple of other options you might consider.")
        for i, route in enumerate(routes[1:3], 2):
            alt_leg = route['legs'][0]
            alt_summary = html.escape(route['summary'])
            alt_duration = alt_leg.get('duration_in_traffic', alt_leg['duration'])['text']
            alt_distance = alt_leg['distance']['text']
            alt_delay = calc_delay_minutes(alt_leg)
            if alt_delay >= 5:
                alt_loc = find_delay_location(alt_leg)
                delay_str = f"with a {alt_delay} minute delay"
                if alt_loc:
                    delay_str += f" near {html.escape(alt_loc)}"
            else:
                delay_str = "with no significant delays"
            parts.append(f"Route {i}: via {alt_summary}, {alt_distance}, approximately {alt_duration}, {delay_str}.")

    parts.append("That covers all your route options. Stay safe out there and have a great drive!")
    return " <break time='600ms'/> ".join(parts)

@app.route('/answer', methods=['POST'])
def answer():
    response = VoiceResponse()
    gather = Gather(num_digits=5, action='/origin', method='POST', timeout=10)
    gather.say(
        "<speak><prosody rate='95%'>Hey there, welcome to the Traffic Hotline. Go ahead and enter your 5 digit origin zip code.</prosody></speak>",
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
        f"<speak><prosody rate='95%'>{msg}</prosody></speak>",
        voice='Polly.Matthew-Neural'
    )
    return str(response)

if __name__ == '__main__':
    app.run(debug=True)
