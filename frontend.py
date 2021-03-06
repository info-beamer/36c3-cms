import os, json, logging, iso8601, random, redis, cPickle
import requests, traceback, tempfile, shutil, hmac, time
from datetime import datetime
from flask import (
    Flask, request, g, session, redirect, url_for,
    jsonify, render_template, sessions, abort
)
from flask.sessions import SessionInterface
from flask_github import GitHub
from werkzeug.contrib.fixers import ProxyFix

log = logging.getLogger('fe')
logging.basicConfig()
logging.getLogger('fe').setLevel(logging.INFO)

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app)
app.config.from_envvar('SETTINGS')

r = redis.Redis()

github = GitHub(app)

class IBHosted(object):
    def __init__(self):
        self._session = requests.Session()
        self._session.auth = '', app.config['HOSTED_API_KEY']

    def get(self, ep, **params):
        r = self._session.get('https://info-beamer.com/api/v1/%s' % ep, params=params, timeout=5)
        r.raise_for_status()
        return r.json()

    def post(self, ep, **data):
        r = self._session.post('https://info-beamer.com/api/v1/%s' % ep, data=data, timeout=5)
        r.raise_for_status()
        return r.json()

    def delete(self, ep, **data):
        r = self._session.delete('https://info-beamer.com/api/v1/%s' % ep, data=data, timeout=5)
        r.raise_for_status()
        return r.json()
ib = IBHosted()

def tojson(v):
    return json.dumps(v, separators=(',', ':'))

def get_user_assets():
    assets = ib.get('asset/list')['assets']
    return [
        dict(
            id = asset['id'],
            filetype = asset['filetype'],
            thumb = asset['thumb'],
            state = asset['userdata'].get('state', 'new'),
            starts = asset['userdata'].get('starts'),
            ends = asset['userdata'].get('ends'),
        )
        for asset in assets
        if asset['userdata'].get('user') == g.user and
           asset['userdata'].get('state') != 'deleted'
    ]

def get_all_live_assets(no_time_filter=False):
    now = int(time.time())
    assets = ib.get('asset/list')['assets']
    return [
        asset
        for asset in assets
        if asset['userdata'].get('state') in ('confirmed',)
        and asset['userdata'].get('user') is not None
        and (no_time_filter or (
            (asset['userdata'].get('starts') or now) <= now and
            (asset['userdata'].get('ends') or now) >= now
        ))
    ]

def get_scoped_api_key(statements, expire=60, uses=16):
    return ib.post('adhoc/create',
        expire = expire,
        uses = uses,
        policy = tojson({
          "Version": 1,
          "Statements":  statements,
        })
    )['api_key']

def update_asset_userdata(asset, **kw):
    userdata = asset['userdata']
    userdata.update(kw)
    ib.post('asset/%d' % asset['id'], userdata=tojson(userdata))

def cached_asset_name(asset):
    filename = 'asset-%d.%s' % (
        asset['id'],
        'jpg' if asset['filetype'] == 'image' else 'mp4',
    )
    cache_name = 'static/%s' % filename
    if not os.path.exists(cache_name):
        log.info("fetching %d" % asset['id'])
        dl = ib.get('asset/%d/download' % asset['id'])
        r = requests.get(dl['download_url'], stream=True, timeout=5)
        r.raise_for_status()
        with tempfile.NamedTemporaryFile() as f:
            shutil.copyfileobj(r.raw, f)
            f.delete = False
            os.rename(f.name, cache_name)
            os.chmod(cache_name, 0o664)
        del r
    return filename

def get_random(size=16):
    return ''.join('%02x' % random.getrandbits(8) for i in range(size))

def mk_sig(value):
    return hmac.new(app.config['URL_KEY'], str(value)).hexdigest()

def error(msg):
    return jsonify(error = msg), 400

class RedisSession(sessions.CallbackDict, sessions.SessionMixin):
    def __init__(self, sid=None, initial=None):
        def on_update(self):
            self.modified = True
        sessions.CallbackDict.__init__(self, initial, on_update)
        self.modified = False
        self.new_sid = not sid
        self.sid = sid or get_random(32)

class RedisSessionStore(SessionInterface):
    def open_session(self, app, request):
        sid = request.cookies.get(app.session_cookie_name)
        if not sid:
            return RedisSession()
        data = r.get('sid:%s' % sid)
        if data is None:
            return RedisSession()
        return RedisSession(sid, cPickle.loads(data))

    def save_session(self, app, session, response):
        if not session.modified:
            return
        state = dict(session)
        if state:
            r.setex('sid:%s' % session.sid, cPickle.dumps(state, 2), 86400)
        else:
            r.delete('sid:%s' % session.sid)
        if session.new_sid:
            response.set_cookie(
                app.session_cookie_name, session.sid,
                httponly=True, secure=True, samesite='Lax'
            )

app.session_interface = RedisSessionStore()

@app.before_request
def before_request():
    g.user = session.get('gh_login')
    # g.user = 'mmuman'
    g.avatar = session.get('gh_avatar')

@app.route('/github-callback')
@github.authorized_handler
def authorized(access_token):
    if access_token is None:
        return redirect(url_for('index'))

    state = request.args.get('state')
    if state is None or state != session.get('state'):
        return redirect(url_for('index'))
    session.pop('state')

    github_user = github.get('user', access_token=access_token)
    if github_user['type'] != 'User':
        return redirect(url_for('faq', _anchor='signup'))

    # import pprint; pprint.pprint(github_user)

    age = datetime.utcnow() - iso8601.parse_date(
        github_user['created_at']
    ).replace(tzinfo=None)

    log.info("user is %d days old" % age.days)
    if age.days < 31:
        return redirect(url_for('faq', _anchor='signup'))

    log.info("user has %d followers" % github_user['followers'])
    if github_user['followers'] < 5:
        return redirect(url_for('faq', _anchor='signup'))

    session['gh_login'] = github_user['login']
    return redirect(url_for('dashboard'))

@app.route('/login')
def login():
    if g.user:
        return redirect(url_for('dashboard'))
    session['state'] = state = get_random()
    return github.authorize(state=state)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/')
def index():
    return render_template('index.jinja')

@app.route('/last')
def last():
    return render_template('last.jinja')

@app.route('/faq')
def faq():
    return render_template('faq.jinja')

@app.route('/interrupt')
def saal():
    interrupt_key = get_scoped_api_key([{
        "Action": "device:node-message",
        "Condition": {
            "StringEquals": {
                "message:path": "root/remote/trigger"
            }
        },
        "Effect": "allow",
    }], expire=300, uses=20)
    return render_template('interrupt.jinja',
        interrupt_key = interrupt_key,
    )

@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.jinja')

@app.route('/sync')
def sync():
    def asset_to_tiles(asset):
        # Change this if you want another page layout for user content
        tiles = []
        if asset['filetype'] == 'video':
            tiles.append({
                "type": "rawvideo",
                "asset": asset['id'],
                "x1":0, "y1":0, "x2":1920, "y2":1080,
                "config": {
                    "layer": -5,
                    "looped": True,
                },
            })
        else:
            tiles.append({
                "type": "image",
                "asset": asset['id'],
                "x1":0, "y1":0 ,"x2":1920, "y2":1080,
            })
        tiles.append({
            "type": "flat",
            "asset": "flat.png",
            "x1":0, "y1":1040, "x2":1920, "y2":1080,
            "config": {
                "color": "#000000",
                "alpha": 230,
                "fade_time": 0
            },
        })
        tiles.append({
            "type": "markup",
            "asset": "default-font.ttf",
            "x1":150, "y1":1048, "x2":1900, "y2":1080,
            "config": {
                "font_size": 25,
                "fade_time": 0.5,
                "text": u"Project by @%s - visit https://36c3.info-beamer.org to share your own." % asset['userdata']['user'],
                "color": "#dddddd"
            },
        })
        tiles.append({
            "type": "image",
            "asset": 92738, # 36c3 logo
            "x1":10, "y1":1090-64, "x2":10+134, "y2":1090,
        })
        return tiles

    pages = []
    for asset in get_all_live_assets():
        pages.append(dict(
            tiles = asset_to_tiles(asset),
            interaction = {'key': ''},
            layout_id = -1, # Use first layout
            overlap = 0,
            auto_duration = 10,
            duration = 10,
        ))

    for setup_id in app.config['SETUP_IDS']:
        config = ib.get("setup/%d" % setup_id)['config']['']
        for schedule in config['schedules']:
            if schedule['name'] == 'User Content':
                schedule['pages'] = pages
        ib.post("setup/%d" % setup_id,
            config = tojson({'': config}),
            mode = 'update',
        )
    r.set('last-sync', int(time.time()))
    return 'ok'

@app.route('/content/list')
def content_list():
    if not g.user:
        return error('Needs login')
    assets = get_user_assets()
    random.shuffle(assets)
    return jsonify(
        assets = assets,
    )

@app.route('/content/upload', methods=['POST'])
def content_upload():
    if not g.user:
        return error('Needs login')
    max_uploads = r.get('max_uploads:%s' % (g.user.encode('utf8'),))
    if max_uploads is not None:
        max_uploads = int(max_uploads)
    if not max_uploads:
        max_uploads = app.config['MAX_UPLOADS']
    if len(get_user_assets()) >= max_uploads:
        return error("You have reached your upload limit")
    filetype = request.values.get('filetype')
    if not filetype in ('image', 'video'):
        return error('Invalid/missing filetype')
    extension = 'jpg' if filetype == 'image' else 'mp4'
    filename = "user/%s/%s %s.%s" % (
        g.user, datetime.utcnow().strftime("%Y-%d-%m %H:%M:%S"),
        os.urandom(16).encode('hex'), extension
    )
    condition = {
        "StringEquals": {
            "asset:filename": filename,
            "asset:filetype": filetype,
            "userdata:user": g.user,
        },
        "NotExists": {
            "userdata:state": True,
        },
        "Boolean": {
            "asset:exists": False,
        }
    }
    if filetype == 'image':
        condition.setdefault("NumericEquals", {}).update({
            "asset:metadata:width": 1920,
            "asset:metadata:height": 1080,
        })
        condition.setdefault("StringEquals", {}).update({
            "asset:metadata:format": "jpeg",
        })
    else:
        condition.setdefault("NumericLess", {}).update({
            "asset:metadata:duration": 11,
        })
        condition.setdefault("StringEquals", {}).update({
            "asset:metadata:format": "h264",
        })
    return jsonify(
        filename = filename,
        user = g.user,
        upload_key = get_scoped_api_key([{
            "Action": "asset:upload",
            "Condition": condition,
            "Effect": "allow"
        }], uses=1)
    )

@app.route('/content/review/<int:asset_id>', methods=['POST'])
def content_request_review(asset_id):
    if not g.user:
        return error('Needs login')
    asset = ib.get('asset/%d' % asset_id)
    if asset['userdata'].get('user') != g.user:
        return error('Cannot review')
    if 'state' in asset['userdata']: # not in new state?
        return error('Cannot review')

    files = {}
    try:
        r = requests.get(asset['thumb'] + '?size=480&crop=none')
        r.raise_for_status()
        files['attachment'] = 'image.jpg', r.content, 'image/jpeg'
    except:
        traceback.print_exc()

    moderation_url = url_for('content_moderate',
        asset_id = asset_id, sig = mk_sig(asset_id),
        _external=True
    )

    requests.post('https://api.pushover.net/1/messages.json',
        data = dict(
            token = app.config['PUSHOVER_APP_KEY'],
            user = app.config['PUSHOVER_TARGET'],
            title = "New moderation request",
            message = u'%s upload from %s.' % (
                asset['filetype'].capitalize(),
                g.user,
            ),
            url = moderation_url,
            url_title = "Moderate content",
        ),
        files = files
    )
    update_asset_userdata(asset, state='review')
    return jsonify(ok = True)

@app.route('/content/moderate/<int:asset_id>-<sig>')
def content_moderate(asset_id, sig):
    if sig != mk_sig(asset_id):
        abort(404)
    try:
        asset = ib.get('asset/%d' % asset_id)
    except:
        traceback.print_exc()
        abort(404)
    state = asset['userdata'].get('state', 'new')
    if state == 'deleted':
        abort(404)
    return render_template('moderate.jinja',
        asset = dict(
            id = asset['id'],
            user = asset['userdata']['user'],
            filetype = asset['filetype'],
            url = url_for('static', filename=cached_asset_name(asset)),
            state = state,
        ),
        sig = mk_sig(asset_id),
    )

@app.route('/content/moderate/<int:asset_id>-<sig>/<any(confirm,reject):result>', methods=['POST'])
def content_moderate_result(asset_id, sig, result):
    if sig != mk_sig(asset_id):
        abort(404)
    try:
        asset = ib.get('asset/%d' % asset_id)
    except:
        traceback.print_exc()
        abort(404)
    if result == "confirm":
        update_asset_userdata(asset, state='confirmed')
    else:
        update_asset_userdata(asset, state='rejected')
    return jsonify(ok = True)

@app.route('/content/<int:asset_id>', methods=['POST'])
def content_update(asset_id):
    if not g.user:
        return error('Needs login')
    asset = ib.get('asset/%d' % asset_id)
    starts = request.values.get('starts', type=int)
    ends = request.values.get('ends', type=int)
    if asset['userdata'].get('user') != g.user:
        return error('Cannot update')
    try:
        update_asset_userdata(asset, starts=starts, ends=ends)
    except:
        traceback.print_exc()
        return error('Cannot update')
    return jsonify(ok = True)

@app.route('/content/<int:asset_id>', methods=['DELETE'])
def content_delete(asset_id):
    if not g.user:
        return error('Needs login')
    asset = ib.get('asset/%d' % asset_id)
    if asset['userdata'].get('user') != g.user:
        return error('Cannot delete')
    try:
        update_asset_userdata(asset, state='deleted')
    except:
        traceback.print_exc()
        return error('Cannot delete')
    return jsonify(ok = True)

@app.route('/content/live')
def content_live():
    no_time_filter = request.values.get('all')
    assets = get_all_live_assets(no_time_filter=no_time_filter)
    random.shuffle(assets)
    resp = jsonify(
        assets = [dict(
            user = asset['userdata']['user'],
            filetype = asset['filetype'],
            thumb = asset['thumb'],
            url = url_for('static', filename=cached_asset_name(asset))
        ) for asset in assets]
    )
    resp.headers['Cache-Control'] = 'public, max-age=30'
    return resp

@app.route('/content/last')
def content_last():
    assets = get_all_live_assets()
    asset_by_id = dict(
        (asset['id'], asset)
        for asset in assets
    )

    last = {}

    for room in app.config['ROOMS']:
        proofs = [
            json.loads(data)
            for data in r.zrange('last:%d' % room['device_id'], 0, -1)
        ]

        last[room['name']] = room_last = []
        for proof in reversed(proofs):
            asset = asset_by_id.get(proof['asset_id'])
            if asset is None:
                continue
            room_last.append(dict(
                id = proof['id'],
                user = asset['userdata']['user'],
                filetype = asset['filetype'],
                shown = int(proof['ts']),
                thumb = asset['thumb'],
                url = url_for('static', filename=cached_asset_name(asset))
            ))
            if len(room_last) > 10:
                break

    resp = jsonify(
        last = [
            [room['name'], last.get(room['name'], [])]
            for room in app.config['ROOMS']
        ]
    )
    resp.headers['Cache-Control'] = 'public, max-age=5'
    return resp

@app.route('/check/sync')
def check_sync():
    if time.time() > int(r.get('last-sync')) + 1200:
        abort(503)
    return 'ok'

@app.route('/check/twitter')
def check_twitter():
    if time.time() > int(r.get('last-twitter')) + 1200:
        abort(503)
    return 'ok'

@app.route('/proof', methods=['POST'])
def proof():
    proofs = [
        (json.loads(row), row)
        for row in request.stream.read().split('\n')
    ]
    device_ids = set()
    p = r.pipeline()
    for proof, row in proofs:
        p.zadd('last:%d' % proof['device_id'], row, proof['ts'])
        device_ids.add(proof['device_id'])
    for device_id in device_ids:
        p.zremrangebyscore('last:%d' % device_id, 0, time.time() - 1200)
    p.execute()
    return "ok"

@app.route('/robots.txt')
def robots_txt():
    return "User-Agent: *\nDisallow: /\n"

if __name__ == '__main__':
    app.run(port=8080)
