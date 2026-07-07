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
