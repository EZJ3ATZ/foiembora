import os, json
import requests as req_lib
from datetime import timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from dotenv import load_dotenv
from models import db, Admin, Seller, User, Subscription, Payment, AccessToken, Commission, Config, FollowerSnapshot, MonitoredProfile, ProfileCountSnapshot, now, gen_token

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

    def _try_fetch(url, headers):
        r = req_lib.get(url, headers=headers, timeout=8)
        if r.status_code != 200:
            return None
        return r.json()

    # Headers realistas de browser desktop
    h1 = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'pt-BR,pt;q=0.9',
        'x-ig-app-id': '936619743392459',
        'Referer': f'https://www.instagram.com/{username}/',
        'Origin': 'https://www.instagram.com',
        'sec-fetch-site': 'same-site',
        'sec-fetch-mode': 'cors',
    }
    # Headers mobile como fallback
    h2 = {
        'User-Agent': 'Instagram 269.0.0.18.75 Android (26/8.0.0; 480dpi; 1080x1920; OnePlus; ONEPLUS A3003; OnePlus3; qcom; pt_BR; 314665256)',
        'Accept-Language': 'pt-BR',
        'x-ig-app-id': '936619743392459',
    }

    try:
        url1 = f'https://i.instagram.com/api/v1/users/web_profile_info/?username={username}'
        data = _try_fetch(url1, h1) or _try_fetch(url1, h2)

        if not data:
            # Fallback: scraping da página pública
            page = req_lib.get(f'https://www.instagram.com/{username}/', headers=h1, timeout=8)
            import re as _re
            m = _re.search(r'"edge_followed_by":\{"count":(\d+)\}', page.text)
            m2 = _re.search(r'"edge_follow":\{"count":(\d+)\}', page.text)
            mn = _re.search(r'"username":"([^"]+)"', page.text)
            if m and m2:
                return jsonify({
                    'username':   mn.group(1) if mn else username,
                    'full_name':  '',
                    'followers':  int(m.group(1)),
                    'following':  int(m2.group(1)),
                    'is_private': False,
                })
            return jsonify({'error': 'Perfil não encontrado, privado ou Instagram indisponível'}), 404

        u = data['data']['user']
        return jsonify({
            'username':   u['username'],
            'full_name':  u.get('full_name', ''),
            'followers':  u['edge_followed_by']['count'],
            'following':  u['edge_follow']['count'],
            'is_private': u.get('is_private', False),
        })
    except Exception as e:
        app.logger.error(f'IG profile error: {e}')
        return jsonify({'error': 'Erro ao buscar perfil. Tente novamente.'}), 500

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
    db_url = app.config['SQLALCHEMY_DATABASE_URI']
    using_sqlite = 'sqlite' in db_url
    return render_template('admin/dashboard.html',
        total_users=total_users, total_sellers=total_sellers,
        active_subs=active_subs, total_revenue=float(total_revenue),
        recent_payments=recent_payments, sellers=sellers,
        using_sqlite=using_sqlite)

@app.route('/admin/usuarios')
@admin_required
def admin_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', users=users)

@app.route('/admin/criar-usuario-teste', methods=['POST'])
@admin_required
def admin_criar_teste():
    from datetime import timedelta
    email    = request.form.get('email','').strip().lower()
    password = request.form.get('password','').strip()
    plan     = request.form.get('plan','trimestral')
    if not email or not password:
        flash('Email e senha obrigatórios.', 'error')
        return redirect(url_for('admin_users'))

    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email)
        db.session.add(user)
    user.set_password(password)
    db.session.flush()

    expires = now() + timedelta(days=90 if plan=='trimestral' else 1)
    sub = Subscription(user_id=user.id, plan=plan, amount=29.90 if plan=='trimestral' else 10.00,
                       status='active', starts_at=now(), expires_at=expires)
    db.session.add(sub)
    db.session.flush()

    token = AccessToken(user_id=user.id, plan=plan, expires_at=expires)
    db.session.add(token)
    db.session.commit()
    flash(f'Usuário {email} criado com plano {plan}. Token: {token.token[:20]}...', 'success')
    return redirect(url_for('admin_users'))

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
    data     = request.json or {}
    email    = data.get('email','').strip().lower()
    plan     = data.get('plan','avulso')
    password = data.get('password','').strip()
    if not email or '@' not in email:
        return jsonify({'error': 'Email inválido'}), 400
    if not password or len(password) < 6:
        return jsonify({'error': 'Senha deve ter ao menos 6 caracteres'}), 400

    amount = 10.00 if plan == 'avulso' else 29.90
    ref    = data.get('ref') or session.get('ref')

    user = User.query.filter_by(email=email).first()
    if not user:
        seller = Seller.query.filter_by(referral_code=ref).first() if ref else None
        user = User(email=email, seller_id=seller.id if seller else None)
        db.session.add(user)
    user.set_password(password)
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

@app.route('/api/db-status')
def db_status():
    db_url = app.config['SQLALCHEMY_DATABASE_URI']
    db_type = 'postgresql' if 'postgresql' in db_url or 'postgres' in db_url else 'sqlite'
    mp_token = bool(os.getenv('MP_ACCESS_TOKEN') or Config.get('MP_ACCESS_TOKEN'))
    return jsonify({
        'db': db_type,
        'mp_configured': mp_token,
        'base_url': os.getenv('BASE_URL', ''),
    })

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

def _send_token_email(to_email, token, plan):
    api_key = os.getenv('RESEND_API_KEY')
    if not api_key:
        app.logger.warning(f"[EMAIL] RESEND_API_KEY ausente. Token para {to_email}: {token}")
        return

    plan_label   = 'Trimestral (3 meses)' if plan == 'trimestral' else 'Avulso (24 horas)'
    plan_cor     = '#a855f7' if plan == 'trimestral' else '#22c55e'
    base_url     = os.getenv('BASE_URL', 'https://foiembora.up.railway.app')

    html = f"""
    <div style="background:#09090b;font-family:Inter,sans-serif;padding:40px 20px;max-width:520px;margin:0 auto">
      <div style="text-align:center;margin-bottom:32px">
        <div style="display:inline-flex;align-items:center;gap:8px;font-size:1.3rem;font-weight:900;color:#f4f4f5">
          <span style="background:linear-gradient(135deg,#833ab4,#fd1d1d,#fcb045);border-radius:8px;width:36px;height:36px;display:inline-flex;align-items:center;justify-content:center">👻</span>
          FoiEmbora
        </div>
      </div>

      <div style="background:#18181c;border:1px solid rgba(255,255,255,0.08);border-radius:16px;padding:32px">
        <h1 style="color:#f4f4f5;font-size:1.4rem;font-weight:800;margin:0 0 8px">🔑 Seu acesso está liberado!</h1>
        <p style="color:#a1a1aa;font-size:0.875rem;margin:0 0 24px">Plano: <strong style="color:{plan_cor}">{plan_label}</strong></p>

        <p style="color:#a1a1aa;font-size:0.875rem;margin:0 0 12px">Seu token de acesso:</p>
        <div style="background:#09090b;border:1px solid rgba(168,85,247,0.3);border-radius:10px;padding:16px;font-family:monospace;font-size:0.82rem;color:#a855f7;word-break:break-all;margin-bottom:24px">
          {token}
        </div>

        <div style="margin-bottom:24px">
          <p style="color:#a1a1aa;font-size:0.82rem;margin:0 0 8px"><strong style="color:#f4f4f5">Como usar:</strong></p>
          <p style="color:#a1a1aa;font-size:0.82rem;margin:0 0 6px">1. Instale a extensão FoiEmbora no Chrome</p>
          <p style="color:#a1a1aa;font-size:0.82rem;margin:0 0 6px">2. Abra o Instagram no navegador</p>
          <p style="color:#a1a1aa;font-size:0.82rem;margin:0 0 6px">3. Clique no ícone da extensão e cole o token acima</p>
          <p style="color:#a1a1aa;font-size:0.82rem;margin:0">4. Clique em Analisar!</p>
        </div>

        <a href="{base_url}/entrar" style="display:block;text-align:center;background:linear-gradient(135deg,#833ab4,#fd1d1d,#fcb045);color:#fff;padding:14px;border-radius:10px;font-weight:700;font-size:0.9rem;text-decoration:none">
          Acessar minha conta →
        </a>
      </div>

      <p style="color:#3f3f46;font-size:0.72rem;text-align:center;margin-top:24px">
        FoiEmbora · foiembora.up.railway.app<br/>
        Guarde esse e-mail — ele contém seu token de acesso.
      </p>
    </div>
    """

    try:
        resp = req_lib.post(
            'https://api.resend.com/emails',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json={
                'from': 'FoiEmbora <onboarding@resend.dev>',
                'to': [to_email],
                'subject': f'🔑 Seu token de acesso FoiEmbora — {plan_label}',
                'html': html,
            },
            timeout=10
        )
        app.logger.info(f"[EMAIL] Enviado para {to_email} — status {resp.status_code}")
    except Exception as e:
        app.logger.error(f"[EMAIL] Falha ao enviar para {to_email}: {e}")

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

# ─── MONITOR / SPY FEATURE ──────────────────────────────────────────────────
def _get_current_user():
    email = session.get('user_email')
    if not email:
        return None
    return User.query.filter_by(email=email).first()

def _fetch_ig_counts(username):
    """Busca contagem de seguidores de um perfil público. Retorna dict ou None."""
    import re as _re

    scraperapi_key = os.getenv('SCRAPERAPI_KEY') or Config.get('SCRAPERAPI_KEY')

    h1 = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': '*/*', 'Accept-Language': 'pt-BR,pt;q=0.9',
        'x-ig-app-id': '936619743392459',
        'Referer': f'https://www.instagram.com/{username}/',
    }

    ig_url = f'https://i.instagram.com/api/v1/users/web_profile_info/?username={username}'

    def _parse_api_response(data):
        try:
            u = data['data']['user']
            return {
                'username':   u['username'],
                'followers':  u['edge_followed_by']['count'],
                'following':  u['edge_follow']['count'],
                'is_private': u.get('is_private', False),
            }
        except Exception:
            return None

    # 1) Tenta direto (funciona em IPs residenciais)
    try:
        r = req_lib.get(ig_url, headers=h1, timeout=8)
        if r.status_code == 200:
            result = _parse_api_response(r.json())
            if result:
                return result
    except Exception:
        pass

    # 2) Tenta via ScraperAPI (proxy residencial rotativo)
    if scraperapi_key:
        try:
            proxy_url = (
                f'http://api.scraperapi.com?api_key={scraperapi_key}'
                f'&url={req_lib.utils.quote(ig_url, safe="")}'
                f'&render=false&country_code=br'
            )
            r = req_lib.get(proxy_url, headers=h1, timeout=30)
            if r.status_code == 200:
                result = _parse_api_response(r.json())
                if result:
                    return result
        except Exception as e:
            app.logger.warning(f'ScraperAPI error for {username}: {e}')

        # 2b) ScraperAPI na página HTML do perfil
        try:
            html_url = f'https://www.instagram.com/{username}/'
            proxy_url2 = (
                f'http://api.scraperapi.com?api_key={scraperapi_key}'
                f'&url={req_lib.utils.quote(html_url, safe="")}'
                f'&render=false&country_code=br'
            )
            r = req_lib.get(proxy_url2, timeout=30)
            if r.status_code == 200:
                m  = _re.search(r'"edge_followed_by":\{"count":(\d+)\}', r.text)
                m2 = _re.search(r'"edge_follow":\{"count":(\d+)\}', r.text)
                if m and m2:
                    return {'username': username, 'followers': int(m.group(1)), 'following': int(m2.group(1)), 'is_private': False}
        except Exception as e:
            app.logger.warning(f'ScraperAPI HTML error for {username}: {e}')

    # 3) Fallback direto na página HTML
    try:
        page = req_lib.get(f'https://www.instagram.com/{username}/', headers=h1, timeout=8)
        m  = _re.search(r'"edge_followed_by":\{"count":(\d+)\}', page.text)
        m2 = _re.search(r'"edge_follow":\{"count":(\d+)\}', page.text)
        if m and m2:
            return {'username': username, 'followers': int(m.group(1)), 'following': int(m2.group(1)), 'is_private': False}
    except Exception:
        pass

    return None

@app.route('/api/monitor/add', methods=['POST'])
def monitor_add():
    user = _get_current_user()
    if not user:
        return jsonify({'error': 'Não autenticado'}), 401
    sub = user.active_subscription
    if not sub:
        return jsonify({'error': 'Assinatura ativa necessária'}), 403

    data = request.json or {}
    username = data.get('username', '').strip().lower().lstrip('@')
    if not username or len(username) < 2:
        return jsonify({'error': 'Username inválido'}), 400

    # Aceita contagens fornecidas pelo frontend (caso o servidor seja bloqueado pelo IG)
    client_followers = data.get('followers')
    client_following = data.get('following')

    limit = 20 if sub.plan == 'trimestral' else 3
    count = MonitoredProfile.query.filter_by(user_id=user.id).count()
    if count >= limit:
        return jsonify({'error': f'Limite de {limit} perfis para o plano {sub.plan}'}), 400

    if MonitoredProfile.query.filter_by(user_id=user.id, username=username).first():
        return jsonify({'error': 'Perfil já monitorado'}), 409

    # Tenta buscar dados do Instagram (pode falhar em IPs de datacenter)
    info = _fetch_ig_counts(username)

    # Se o servidor falhou mas o frontend forneceu os dados, usa eles
    if not info and client_followers is not None:
        info = {
            'username': username,
            'followers': int(client_followers),
            'following': int(client_following or 0),
            'is_private': False
        }

    profile = MonitoredProfile(user_id=user.id, username=username)
    db.session.add(profile)
    db.session.flush()

    has_data = bool(info)
    if info:
        snap = ProfileCountSnapshot(
            profile_id=profile.id,
            followers=info['followers'],
            following=info['following'],
            is_private=info.get('is_private', False)
        )
        db.session.add(snap)

    db.session.commit()
    return jsonify({
        'ok': True,
        'username': username,
        'followers': info['followers'] if info else 0,
        'following': info['following'] if info else 0,
        'has_data': has_data,
        'message': None if has_data else 'Perfil adicionado! Instagram bloqueou a busca automática. Clique "Verificar" para tentar novamente.'
    })

@app.route('/api/monitor/list')
def monitor_list():
    user = _get_current_user()
    if not user:
        return jsonify({'error': 'Não autenticado'}), 401
    profiles = MonitoredProfile.query.filter_by(user_id=user.id).order_by(MonitoredProfile.created_at.desc()).all()
    result = []
    for p in profiles:
        snaps = p.snapshots.order_by(ProfileCountSnapshot.created_at.desc()).limit(2).all()
        latest = snaps[0] if snaps else None
        prev   = snaps[1] if len(snaps) > 1 else None
        result.append({
            'username':   p.username,
            'followers':  latest.followers if latest else 0,
            'following':  latest.following if latest else 0,
            'is_private': latest.is_private if latest else False,
            'diff':       (latest.followers - prev.followers) if (latest and prev) else None,
            'last_check': latest.created_at.strftime('%d/%m %H:%M') if latest else None,
        })
    return jsonify({'ok': True, 'profiles': result})

@app.route('/api/monitor/check/<username>', methods=['POST'])
def monitor_check(username):
    user = _get_current_user()
    if not user:
        return jsonify({'error': 'Não autenticado'}), 401
    username = username.lower().lstrip('@')
    profile = MonitoredProfile.query.filter_by(user_id=user.id, username=username).first()
    if not profile:
        return jsonify({'error': 'Perfil não monitorado'}), 404

    info = _fetch_ig_counts(username)
    if not info:
        return jsonify({'error': 'Erro ao buscar perfil'}), 500

    prev = profile.snapshots.order_by(ProfileCountSnapshot.created_at.desc()).first()
    snap = ProfileCountSnapshot(
        profile_id=profile.id,
        followers=info['followers'],
        following=info['following'],
        is_private=info['is_private']
    )
    db.session.add(snap)
    db.session.commit()

    diff = info['followers'] - prev.followers if prev else 0
    return jsonify({'ok': True, 'followers': info['followers'], 'following': info['following'], 'diff': diff})

@app.route('/api/monitor/history/<username>')
def monitor_history(username):
    user = _get_current_user()
    if not user:
        return jsonify({'error': 'Não autenticado'}), 401
    username = username.lower().lstrip('@')
    profile = MonitoredProfile.query.filter_by(user_id=user.id, username=username).first()
    if not profile:
        return jsonify({'error': 'Perfil não encontrado'}), 404
    snaps = profile.snapshots.order_by(ProfileCountSnapshot.created_at.asc()).all()
    history = [{'date': s.created_at.strftime('%d/%m %H:%M'), 'followers': s.followers, 'following': s.following} for s in snaps]
    return jsonify({'ok': True, 'username': username, 'history': history})

@app.route('/api/monitor/remove/<username>', methods=['DELETE'])
def monitor_remove(username):
    user = _get_current_user()
    if not user:
        return jsonify({'error': 'Não autenticado'}), 401
    username = username.lower().lstrip('@')
    profile = MonitoredProfile.query.filter_by(user_id=user.id, username=username).first()
    if not profile:
        return jsonify({'error': 'Perfil não encontrado'}), 404
    db.session.delete(profile)
    db.session.commit()
    return jsonify({'ok': True})

# ─── USER LOGIN / DASHBOARD ─────────────────────────────────────────────────
@app.route('/entrar', methods=['GET', 'POST'])
def user_login():
    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '').strip()
        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            flash('Email ou senha incorretos.', 'error')
            return redirect(url_for('user_login'))
        session['user_email'] = email
        return redirect(url_for('minha_conta'))
    return render_template('user/login.html')

@app.route('/sair-usuario')
def user_logout():
    session.pop('user_email', None)
    return redirect(url_for('index'))

@app.route('/minha-conta')
def minha_conta():
    import json as _json
    email = session.get('user_email')
    if not email:
        return redirect(url_for('user_login'))
    user = User.query.filter_by(email=email).first()
    if not user:
        session.pop('user_email', None)
        return redirect(url_for('user_login'))

    t = user.tokens.order_by(AccessToken.created_at.desc()).first()

    # Snapshots da extensão (meu próprio Instagram)
    history = []
    if t:
        snaps = t.snapshots.order_by(FollowerSnapshot.created_at.desc()).limit(30).all()
        for i, s in enumerate(snaps):
            followers_now = set(_json.loads(s.followers))
            following_now = set(_json.loads(s.following))
            unfollowers = new_followers = []
            if i < len(snaps) - 1:
                prev = snaps[i + 1]
                followers_prev = set(_json.loads(prev.followers))
                unfollowers   = sorted(followers_prev - followers_now)
                new_followers = sorted(followers_now  - followers_prev)
            history.append({
                'date':          s.created_at.strftime('%d/%m/%Y %H:%M'),
                'followers':     len(followers_now),
                'following':     len(following_now),
                'unfollowers':   unfollowers,
                'new_followers': new_followers,
            })

    # Perfis monitorados (spy feature)
    monitored = []
    for mp in user.monitored_profiles.order_by(MonitoredProfile.created_at.desc()).all():
        snaps_mp = mp.snapshots.order_by(ProfileCountSnapshot.created_at.desc()).limit(30).all()
        snap_history = [{'date': s.created_at.strftime('%d/%m %H:%M'), 'followers': s.followers, 'following': s.following} for s in reversed(snaps_mp)]
        latest_snap = snaps_mp[0] if snaps_mp else None
        prev_snap   = snaps_mp[1] if len(snaps_mp) > 1 else None
        monitored.append({
            'username':   mp.username,
            'followers':  latest_snap.followers if latest_snap else 0,
            'following':  latest_snap.following if latest_snap else 0,
            'is_private': latest_snap.is_private if latest_snap else False,
            'diff':       (latest_snap.followers - prev_snap.followers) if (latest_snap and prev_snap) else None,
            'last_check': latest_snap.created_at.strftime('%d/%m %H:%M') if latest_snap else None,
            'history':    snap_history,
        })

    sub = user.active_subscription
    plan_limit = 20 if (sub and sub.plan == 'trimestral') else 3

    return render_template('user/dashboard.html',
        token=t,
        history=history,
        latest=history[0] if history else None,
        monitored=monitored,
        plan_limit=plan_limit,
        has_active_sub=bool(sub),
    )

# ─── STATIC ─────────────────────────────────────────────────────────────────
@app.route('/static/<path:filename>')
def static_files(filename):
    return send_file(os.path.join(os.path.dirname(__file__), 'static', filename))

if __name__ == '__main__':
    app.run(debug=True, port=5050)
