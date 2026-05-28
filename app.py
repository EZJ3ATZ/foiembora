import os, json
import requests as req_lib
from datetime import timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from dotenv import load_dotenv
from models import db, Admin, Seller, User, Subscription, Payment, AccessToken, Commission, Config, FollowerSnapshot, now, gen_token

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-change-in-prod')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///foiembora.db').replace('postgres://', 'postgresql://')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# Jinja2 filters
app.jinja_env.globals['enumerate'] = enumerate

login_manager = LoginManager(app)
login_manager.login_view = 'admin_login'

@login_manager.user_loader
def load_user(user_id):
    if user_id.startswith('admin:'):
        return Admin.query.get(int(user_id.split(':')[1]))
    if user_id.startswith('seller:'):
        return Seller.query.get(int(user_id.split(':')[1]))
    return None

# ─── INIT DB ───────────────────────────────────────────────────────────────
with app.app_context():
    db.create_all()
    # Cria ou atualiza admin com as credenciais das env vars
    _email = os.getenv('ADMIN_EMAIL', 'mathcostaz')
    _pw    = os.getenv('ADMIN_PASSWORD', 'Am241845!@#$%')
    admin  = Admin.query.first()
    if not admin:
        admin = Admin(email=_email)
        db.session.add(admin)
    else:
        admin.email = _email
    admin.set_password(_pw)
    db.session.commit()

# ─── HELPERS ───────────────────────────────────────────────────────────────
def is_admin():
    return current_user.is_authenticated and isinstance(current_user, Admin)

def is_seller():
    return current_user.is_authenticated and isinstance(current_user, Seller)

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_admin():
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated

def seller_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_seller():
            return redirect(url_for('seller_login'))
        return f(*args, **kwargs)
    return decorated

# ─── PÚBLICO ───────────────────────────────────────────────────────────────
@app.route('/')
def index():
    ref = request.args.get('ref')
    if ref:
        session['ref'] = ref
    return render_template('index.html')

@app.route('/pitch')
def pitch():
    return send_file(os.path.join(os.path.dirname(__file__), 'pitch.html'))

@app.route('/download/extensao')
def download_extensao():
    path = os.path.join(os.path.dirname(__file__), 'foiembora-extensao.zip')
    return send_file(path, as_attachment=True, download_name='FoiEmbora-extensao.zip')

# ─── INSTAGRAM PUBLIC PROFILE ───────────────────────────────────────────────
@app.route('/api/instagram/profile')
def instagram_profile():
    username = request.args.get('username', '').strip().lower().lstrip('@')
    if not username:
        return jsonify({'error': 'Username required'}), 400
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 9; GM1903) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/76.0.3809.89 Mobile Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8',
            'x-ig-app-id': '936619743392459',
            'Referer': 'https://www.instagram.com/',
            'Origin': 'https://www.instagram.com',
        }
        url = f'https://i.instagram.com/api/v1/users/web_profile_info/?username={username}'
        r = req_lib.get(url, headers=headers, timeout=8)
        if r.status_code != 200:
            return jsonify({'error': 'Perfil não encontrado ou conta privada'}), 404
        data = r.json()
        u = data['data']['user']
        return jsonify({
            'username':    u['username'],
            'full_name':   u.get('full_name', ''),
            'followers':   u['edge_followed_by']['count'],
            'following':   u['edge_follow']['count'],
            'is_private':  u.get('is_private', False),
            'biography':   u.get('biography', ''),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ─── AUTH ADMIN ─────────────────────────────────────────────────────────────
@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    if is_admin():
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        admin = Admin.query.filter_by(email=request.form['email']).first()
        if admin and admin.check_password(request.form['password']):
            login_user(admin)
            return redirect(url_for('admin_dashboard'))
        flash('Email ou senha incorretos.', 'error')
    return render_template('admin/login.html', role='admin')

@app.route('/admin/logout')
def admin_logout():
    logout_user()
    return redirect(url_for('admin_login'))

# ─── AUTH SELLER ────────────────────────────────────────────────────────────
@app.route('/vendedor/login', methods=['GET','POST'])
def seller_login():
    if is_seller():
        return redirect(url_for('seller_dashboard'))
    if request.method == 'POST':
        seller = Seller.query.filter_by(email=request.form['email']).first()
        if seller and seller.check_password(request.form['password']) and seller.is_active:
            login_user(seller)
            return redirect(url_for('seller_dashboard'))
        flash('Email ou senha incorretos.', 'error')
    return render_template('admin/login.html', role='seller')

@app.route('/vendedor/logout')
def seller_logout():
    logout_user()
    return redirect(url_for('seller_login'))

# ─── ADMIN DASHBOARD ────────────────────────────────────────────────────────
@app.route('/admin')
@admin_required
def admin_dashboard():
    from sqlalchemy import func
    total_users    = User.query.count()
    total_sellers  = Seller.query.count()
    active_subs    = Subscription.query.filter_by(status='active').count()
    total_revenue  = db.session.query(func.sum(Payment.amount))\
                       .filter(Payment.status == 'approved').scalar() or 0
    recent_payments = Payment.query.order_by(Payment.created_at.desc()).limit(10).all()
    sellers        = Seller.query.order_by(Seller.created_at.desc()).all()
    return render_template('admin/dashboard.html',
        total_users=total_users, total_sellers=total_sellers,
        active_subs=active_subs, total_revenue=float(total_revenue),
        recent_payments=recent_payments, sellers=sellers)

@app.route('/admin/usuarios')
@admin_required
def admin_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', users=users)

@app.route('/admin/assinaturas')
@admin_required
def admin_subscriptions():
    subs = Subscription.query.order_by(Subscription.created_at.desc()).all()
    return render_template('admin/subscriptions.html', subs=subs)

@app.route('/admin/pagamentos')
@admin_required
def admin_payments():
    payments = Payment.query.order_by(Payment.created_at.desc()).all()
    return render_template('admin/payments.html', payments=payments)

@app.route('/admin/vendedores')
@admin_required
def admin_sellers():
    sellers = Seller.query.order_by(Seller.created_at.desc()).all()
    return render_template('admin/sellers.html', sellers=sellers)

@app.route('/admin/vendedores/novo', methods=['POST'])
@admin_required
def admin_seller_create():
    name  = request.form.get('name','').strip()
    email = request.form.get('email','').strip()
    pw    = request.form.get('password','').strip()
    rate  = float(request.form.get('commission_rate', 30))
    if not name or not email or not pw:
        flash('Preencha todos os campos.', 'error')
        return redirect(url_for('admin_sellers'))
    if Seller.query.filter_by(email=email).first():
        flash('Email já cadastrado.', 'error')
        return redirect(url_for('admin_sellers'))
    s = Seller(name=name, email=email, commission_rate=rate)
    s.set_password(pw)
    db.session.add(s)
    db.session.commit()
    flash(f'Vendedor {name} criado com código {s.referral_code}.', 'success')
    return redirect(url_for('admin_sellers'))

@app.route('/admin/vendedores/<int:sid>/toggle', methods=['POST'])
@admin_required
def admin_seller_toggle(sid):
    s = Seller.query.get_or_404(sid)
    s.is_active = not s.is_active
    db.session.commit()
    return redirect(url_for('admin_sellers'))

@app.route('/admin/comissoes/<int:cid>/pagar', methods=['POST'])
@admin_required
def admin_commission_pay(cid):
    c = Commission.query.get_or_404(cid)
    c.status = 'paid'
    c.paid_at = now()
    db.session.commit()
    return redirect(url_for('admin_sellers'))

@app.route('/admin/configuracoes', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    if request.method == 'POST':
        mp_token = request.form.get('mp_token', '').strip()
        base_url  = request.form.get('base_url', '').strip()
        try:
            if mp_token:
                Config.set('MP_ACCESS_TOKEN', mp_token)
            if base_url:
                Config.set('BASE_URL', base_url)
            db.session.commit()
            flash('Configurações salvas!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao salvar: {e}', 'error')
        return redirect(url_for('admin_settings'))

    mp_token_saved = Config.get('MP_ACCESS_TOKEN', '')
    base_url_saved = Config.get('BASE_URL', os.getenv('BASE_URL', ''))
    mp_status = None
    mp_balance = None

    mp_token = mp_token_saved or os.getenv('MP_ACCESS_TOKEN', '')
    if mp_token:
        try:
            import mercadopago
            sdk = mercadopago.SDK(mp_token)
            resp = sdk.payment().search({"filters": {"status": "approved"}, "limit": 1})
            mp_status = 'ok' if resp.get('status') == 200 else 'error'
        except Exception as e:
            mp_status = 'error'

    base_url_display = base_url_saved or request.host_url.rstrip('/')
    return render_template('admin/settings.html',
        mp_token_saved=mp_token_saved,
        mp_token_configured=bool(mp_token),
        mp_status=mp_status,
        base_url=base_url_display,
        webhook_url=f"{base_url_display}/api/webhook/mp"
    )

@app.route('/admin/trocar-senha', methods=['POST'])
@admin_required
def admin_change_password():
    pw = request.form.get('new_password', '').strip()
    if len(pw) >= 8:
        current_user.set_password(pw)
        db.session.commit()
        flash('Senha alterada com sucesso.', 'success')
    else:
        flash('Senha deve ter ao menos 8 caracteres.', 'error')
    return redirect(url_for('admin_settings'))

# ─── SELLER DASHBOARD ───────────────────────────────────────────────────────
@app.route('/vendedor')
@seller_required
def seller_dashboard():
    seller = current_user
    commissions = Commission.query.filter_by(seller_id=seller.id)\
                    .order_by(Commission.created_at.desc()).all()
    subs = Subscription.query.filter_by(seller_id=seller.id)\
             .order_by(Subscription.created_at.desc()).limit(20).all()
    base_url = os.getenv('BASE_URL', request.host_url.rstrip('/'))
    referral_link = f"{base_url}/?ref={seller.referral_code}"
    return render_template('seller/dashboard.html',
        seller=seller, commissions=commissions,
        subs=subs, referral_link=referral_link)

# ─── API PAGAMENTO ───────────────────────────────────────────────────────────
@app.route('/api/checkout', methods=['POST'])
def checkout():
    data  = request.json or {}
    email = data.get('email','').strip().lower()
    plan  = data.get('plan','avulso')
    if not email or '@' not in email:
        return jsonify({'error': 'Email inválido'}), 400

    amount = 10.00 if plan == 'avulso' else 29.90
    ref    = data.get('ref') or session.get('ref')

    user = User.query.filter_by(email=email).first()
    if not user:
        seller = Seller.query.filter_by(referral_code=ref).first() if ref else None
        user = User(email=email, seller_id=seller.id if seller else None)
        db.session.add(user)
        db.session.flush()

    sub = Subscription(user_id=user.id, plan=plan, amount=amount,
                       seller_id=user.seller_id, status='pending')
    db.session.add(sub)
    db.session.flush()

    payment = Payment(subscription_id=sub.id, amount=amount, method='pix', status='pending')
    db.session.add(payment)
    db.session.commit()

    # Integração Mercado Pago (env tem prioridade, senão usa banco)
    mp_token = os.getenv('MP_ACCESS_TOKEN') or Config.get('MP_ACCESS_TOKEN')
    pix_data = None
    if mp_token:
        try:
            import mercadopago
            sdk = mercadopago.SDK(mp_token)
            base_url = os.getenv('BASE_URL', request.host_url.rstrip('/'))
            payload = {
                "transaction_amount": amount,
                "description": f"FoiEmbora — Plano {plan.capitalize()}",
                "payment_method_id": "pix",
                "payer": {"email": email},
                "notification_url": f"{base_url}/api/webhook/mp",
                "external_reference": str(payment.id)
            }
            resp = sdk.payment().create(payload)
            if resp["status"] == 201:
                mp_id = resp["response"]["id"]
                qr    = resp["response"]["point_of_interaction"]["transaction_data"]["qr_code"]
                qr_b64= resp["response"]["point_of_interaction"]["transaction_data"]["qr_code_base64"]
                payment.mp_payment_id = str(mp_id)
                payment.pix_code = qr
                db.session.commit()
                pix_data = {"qr_code": qr, "qr_base64": qr_b64, "payment_id": payment.id}
        except Exception as e:
            app.logger.error(f"MP error: {e}")

    if not pix_data:
        # Modo demo sem MP configurado
        pix_data = {"qr_code": "PIX_DEMO_00020126330014BR.GOV.BCB.PIX", "payment_id": payment.id}

    return jsonify({"ok": True, "pix": pix_data, "payment_id": payment.id})

@app.route('/api/webhook/mp', methods=['POST'])
def mp_webhook():
    data = request.json or {}
    if data.get('type') != 'payment':
        return jsonify({'ok': True})

    mp_id = str(data.get('data',{}).get('id',''))
    payment = Payment.query.filter_by(mp_payment_id=mp_id).first()
    if not payment:
        return jsonify({'ok': True})

    mp_token = os.getenv('MP_ACCESS_TOKEN') or Config.get('MP_ACCESS_TOKEN')
    if mp_token:
        import mercadopago
        sdk = mercadopago.SDK(mp_token)
        resp = sdk.payment().get(mp_id)
        if resp["status"] == 200 and resp["response"]["status"] == "approved":
            _approve_payment(payment)

    return jsonify({'ok': True})

@app.route('/api/payment/<int:pid>/status')
def payment_status(pid):
    p = Payment.query.get_or_404(pid)
    return jsonify({'status': p.status, 'payment_id': p.id})

def _approve_payment(payment):
    if payment.status == 'approved':
        return
    payment.status = 'approved'
    sub = payment.subscription

    from datetime import datetime, timezone
    sub.status    = 'active'
    sub.starts_at = now()
    sub.expires_at = now() + (timedelta(days=1) if sub.plan == 'avulso' else timedelta(days=90))

    token = AccessToken(user_id=sub.user_id, plan=sub.plan, expires_at=sub.expires_at)
    db.session.add(token)

    # Gera comissão se veio de vendedor
    if sub.seller_id:
        seller = Seller.query.get(sub.seller_id)
        if seller:
            comm_amount = float(payment.amount) * float(seller.commission_rate) / 100
            commission = Commission(seller_id=seller.id, payment_id=payment.id, amount=comm_amount)
            db.session.add(commission)

    db.session.commit()

    # Envia token por email (quando configurado)
    _send_token_email(sub.user.email, token.token, sub.plan)

def _send_token_email(email, token, plan):
    # Placeholder — implementar com SendGrid ou similar
    app.logger.info(f"TOKEN para {email} ({plan}): {token}")

# ─── API EXTENSÃO ────────────────────────────────────────────────────────────
@app.route('/api/token/validate')
def token_validate():
    token = request.args.get('token','')
    t = AccessToken.query.filter_by(token=token).first()
    if not t or not t.is_valid:
        return jsonify({'valid': False}), 401
    return jsonify({'valid': True, 'plan': t.plan, 'expires_at': t.expires_at.isoformat() if t.expires_at else None})

@app.route('/api/snapshot', methods=['POST'])
def snapshot_save():
    """Salva snapshot de followers/following e retorna diff com snapshot anterior."""
    import json as _json
    data      = request.json or {}
    token_str = data.get('token', '')
    followers = data.get('followers', [])   # lista de usernames
    following = data.get('following', [])   # lista de usernames

    t = AccessToken.query.filter_by(token=token_str).first()
    if not t or not t.is_valid:
        return jsonify({'error': 'Token inválido'}), 401
    if t.plan != 'trimestral':
        return jsonify({'error': 'Recurso exclusivo do plano Trimestral'}), 403

    # Snapshot anterior (mais recente)
    prev = t.snapshots.order_by(FollowerSnapshot.created_at.desc()).first()

    unfollowers = []
    new_followers = []

    if prev:
        prev_followers = set(_json.loads(prev.followers))
        curr_followers = set(followers)
        unfollowers    = list(prev_followers - curr_followers)   # pararam de seguir
        new_followers  = list(curr_followers - prev_followers)   # começaram a seguir
        prev_date      = prev.created_at.strftime('%d/%m/%Y')
    else:
        prev_date = None

    # Salva novo snapshot
    snap = FollowerSnapshot(
        token_id  = t.id,
        followers = _json.dumps(followers),
        following = _json.dumps(following)
    )
    db.session.add(snap)
    db.session.commit()

    return jsonify({
        'ok': True,
        'unfollowers':    unfollowers,
        'new_followers':  new_followers,
        'prev_date':      prev_date,
        'total_snapshots': t.snapshots.count()
    })

@app.route('/api/snapshot/history')
def snapshot_history():
    """Retorna histórico de contagens de seguidores do token."""
    import json as _json
    token_str = request.args.get('token', '')
    t = AccessToken.query.filter_by(token=token_str).first()
    if not t or not t.is_valid:
        return jsonify({'error': 'Token inválido'}), 401
    if t.plan != 'trimestral':
        return jsonify({'error': 'Recurso exclusivo do plano Trimestral'}), 403

    snaps = t.snapshots.order_by(FollowerSnapshot.created_at.desc()).limit(30).all()
    history = [{
        'date':      s.created_at.strftime('%d/%m/%Y %H:%M'),
        'followers': len(_json.loads(s.followers)),
        'following': len(_json.loads(s.following)),
    } for s in snaps]

    return jsonify({'ok': True, 'history': history})

# ─── USER LOGIN / DASHBOARD ─────────────────────────────────────────────────
@app.route('/entrar', methods=['GET', 'POST'])
def user_login():
    if request.method == 'POST':
        token_str = request.form.get('token', '').strip()
        t = AccessToken.query.filter_by(token=token_str).first()
        if not t or not t.is_valid:
            flash('Token inválido ou expirado.', 'error')
            return redirect(url_for('user_login'))
        session['user_token'] = token_str
        return redirect(url_for('minha_conta'))
    return render_template('user/login.html')

@app.route('/sair-usuario')
def user_logout():
    session.pop('user_token', None)
    return redirect(url_for('index'))

@app.route('/minha-conta')
def minha_conta():
    import json as _json
    token_str = session.get('user_token')
    if not token_str:
        return redirect(url_for('user_login'))
    t = AccessToken.query.filter_by(token=token_str).first()
    if not t or not t.is_valid:
        session.pop('user_token', None)
        return redirect(url_for('user_login'))

    # Snapshots e diff de unfollowers
    snaps = t.snapshots.order_by(FollowerSnapshot.created_at.desc()).limit(30).all()
    history = []
    for i, s in enumerate(snaps):
        followers_now  = set(_json.loads(s.followers))
        following_now  = set(_json.loads(s.following))
        unfollowers = []
        new_followers = []
        if i < len(snaps) - 1:
            prev = snaps[i + 1]
            followers_prev = set(_json.loads(prev.followers))
            unfollowers   = sorted(followers_prev - followers_now)
            new_followers = sorted(followers_now  - followers_prev)
        history.append({
            'date':         s.created_at.strftime('%d/%m/%Y %H:%M'),
            'followers':    len(followers_now),
            'following':    len(following_now),
            'unfollowers':  unfollowers,
            'new_followers': new_followers,
        })

    return render_template('user/dashboard.html',
        token=t,
        history=history,
        latest=history[0] if history else None,
    )

# ─── STATIC ─────────────────────────────────────────────────────────────────
@app.route('/static/<path:filename>')
def static_files(filename):
    return send_file(os.path.join(os.path.dirname(__file__), 'static', filename))

if __name__ == '__main__':
    app.run(debug=True, port=5050)
