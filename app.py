from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import requests
import os

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
    except Exception:
        return None

@app.route('/answer', methods=['GET', 'POST'])
def answer():
    resp = VoiceResponse()
    gather = Gather(num_digits=5, action='/got_origin', method='POST', timeout=10)
    gather.say("Welcome to the Traffic Line! Please enter your 5-digit origin zip code.", voice='Polly.Matthew')
    resp.append(gather)
    resp.say("We did not receive any input. Please call back and try again.", voice='Polly.Matthew')
    return Response(str(resp), mimetype='text/xml')

@app.route('/got_origin', methods=['GET', 'POST'])
def got_origin():
    origin_zip = request.form.get('Digits', '')
    resp = VoiceResponse()
    gather = Gather(num_digits=5, action=f'/got_destination?origin={origin_zip}', method='POST', timeout=10)
    gather.say("Thank you. Now please enter your 5-digit destination zip code.", voice='Polly.Matthew')
    resp.append(gather)
    resp.say("We did not receive any input. Please call back and try again.", voice='Polly.Matthew')
    return Response(str(resp), mimetype='text/xml')

@app.route('/got_destination', methods=['GET', 'POST'])
def got_destination():
    dest_zip = request.form.get('Digits', '')
    origin_zip = request.args.get('origin', '')
    resp = VoiceResponse()
    directions = get_directions(origin_zip, dest_zip)
    if directions:
        msg = (
            f"Here is your route. The best way from zip code {' '.join(origin_zip)} "
            f"to zip code {' '.join(dest_zip)} is via {directions['summary']}. "
            f"Current travel time is {directions['duration']}, "
            f"covering {directions['distance']}."
            f"{directions['alt_info']} Have a safe trip!"
        )
    else:
        msg = "Sorry, we could not find a route for those zip codes. Please check the numbers and call back."
    resp.say(msg, voice='Polly.Matthew')
    return Response(str(resp), mimetype='text/xml')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
