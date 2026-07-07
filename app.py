import os
import requests
from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Gather

app = Flask(__name__)

GOOGLE_MAPS_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY')

def get_directions(origin_zip, dest_zip):
    url = "https://maps.googleapis.com/maps/api/directions/json"
    params = {
        'origin': origin_zip,
        'destination': dest_zip,
        'departure_time': 'now',
        'traffic_model': 'best_guess',
        'alternatives': True,
        'key': GOOGLE_MAPS_API_KEY
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        print(f"Maps API status: {data['status']}")
        if 'error_message' in data:
            print(f"Maps API error: {data['error_message']}")
        if data['status'] != 'OK':
            return None
        routes = data['routes']
        best = routes[0]
        leg = best['legs'][0]
        duration = leg.get('duration_in_traffic', leg['duration'])['text']
        distance = leg['distance']['text']
        summary = best['summary']
        alt_info = ""
        if len(routes) > 1:
            alt_leg = routes[1]['legs'][0]
            alt_dur = alt_leg.get('duration_in_traffic', alt_leg['duration'])['text']
            alt_sum = routes[1]['summary']
            alt_info = f" An alternate route via {alt_sum} would take {alt_dur}."
        return {'summary': summary, 'duration': duration, 'distance': distance, 'alt_info': alt_info}
    except Exception as e:
        print(f"Exception in get_directions: {e}")
        return None

@app.route('/answer', methods=['POST'])
def answer():
    response = VoiceResponse()
    gather = Gather(num_digits=5, action='/origin', method='POST', timeout=10)
    gather.say("Welcome to the traffic hotline. Please enter your 5 digit origin zip code.", voice='Polly.Matthew')
    response.append(gather)
    return str(response)

@app.route('/origin', methods=['POST'])
def origin():
    origin_zip = request.form.get('Digits', '')
    response = VoiceResponse()
    gather = Gather(num_digits=5, action=f'/result?origin={origin_zip}', method='POST', timeout=10)
    gather.say("Now enter your 5 digit destination zip code.", voice='Polly.Matthew')
    response.append(gather)
    return str(response)

@app.route('/result', methods=['POST'])
def result():
    origin_zip = request.args.get('origin', '')
    dest_zip = request.form.get('Digits', '')
    response = VoiceResponse()
    directions = get_directions(origin_zip, dest_zip)
    if directions:
        msg = (
            f"The fastest route from {' '.join(origin_zip)} to {' '.join(dest_zip)} "
            f"is via {directions['summary']}, "
            f"taking {directions['duration']} "
            f"over {directions['distance']}."
            f"{directions['alt_info']}"
            f" Drive safe!"
        )
    else:
        msg = "Sorry, I could not find a route for those zip codes. Please try again."
    response.say(msg, voice='Polly.Matthew')
    return str(response)

if __name__ == '__main__':
    app.run(debug=True)
