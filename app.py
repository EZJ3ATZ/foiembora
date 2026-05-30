import os, json
import requests as req_lib
from datetime import timedelta, timezone

# Fuso de Brasília (UTC-3, sem horário de verão desde 2019).
_BR_TZ = timezone(timedelta(hours=-3))
def br_dt(dt, fmt='%d/%m/%Y %H:%M'):
    """Converte um datetime (UTC, com ou sem tzinfo) para horário de Brasília e formata."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_BR_TZ).strftime(fmt)
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from dotenv import load_dotenv
from models import db, Admin, Seller, User, Subscription, Payment, AccessToken, Commission, Config, FollowerSnapshot, MonitoredProfile, ProfileCountSnapshot, SpyFollowerSnapshot, now, gen_token

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-change-in-prod')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///foiembora.db').replace('postgres://', 'postgresql://')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# ─── CORS p/ extensão (content script no instagram.com chama nossos endpoints) ─
# Orion/WebKit (iOS) NÃO faz o bypass de CORS que o Chrome faz via host_permissions.
# Sem estes headers, o POST cross-origin de www.instagram.com -> foiembora falha
# com "Load failed" e derruba o spy. Liberamos as origens da extensão.
_CORS_ORIGINS = ('https://www.instagram.com', 'https://instagram.com')

@app.after_request
def _add_cors(resp):
    origin = request.headers.get('Origin', '')
    if origin in _CORS_ORIGINS:
        resp.headers['Access-Control-Allow-Origin']  = origin
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        resp.headers['Vary'] = 'Origin'
    return resp

@app.before_request
def _handle_preflight():
    if request.method == 'OPTIONS':
        origin = request.headers.get('Origin', '')
        if origin in _CORS_ORIGINS:
            return ('', 204)

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

    # Aceita contagens vindas do cliente (extensão/sessão logada) — caminho grátis e 100%
    cf = request.args.get('followers')
    cg = request.args.get('following')
    if cf is not None and str(cf).isdigit():
        return jsonify({
            'username': username, 'full_name': '',
            'followers': int(cf), 'following': int(cg) if (cg and str(cg).isdigit()) else 0,
            'is_private': False, 'photo_url': '', 'source': 'client',
        })

    info = _ig_profile(username)
    if not info:
        return jsonify({'error': 'Perfil não encontrado, privado ou Instagram indisponível'}), 404
    info['source'] = 'server'
    return jsonify(info)

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

@app.route('/admin/usuario/<int:user_id>/token')
@admin_required
def admin_ver_token(user_id):
    """Retorna o token de acesso mais recente do usuário."""
    token = AccessToken.query.filter_by(user_id=user_id).order_by(AccessToken.created_at.desc()).first()
    if not token:
        return jsonify({'token': None, 'error': 'Nenhum token gerado para este usuário'})
    return jsonify({'token': token.token, 'plan': token.plan, 'valid': token.is_valid})

@app.route('/admin/usuario/<int:user_id>/senha', methods=['POST'])
@admin_required
def admin_set_senha(user_id):
    """Admin define nova senha para um usuário."""
    user = User.query.get(user_id)
    if not user:
        return jsonify({'ok': False, 'error': 'Usuário não encontrado'}), 404
    data = request.json or {}
    password = data.get('password', '').strip()
    if len(password) < 4:
        return jsonify({'ok': False, 'error': 'Senha muito curta (mínimo 4 caracteres)'}), 400
    user.set_password(password)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/admin/usuario/<int:user_id>/apagar', methods=['DELETE'])
@admin_required
def admin_apagar_usuario(user_id):
    """Remove usuário e todos os dados associados."""
    user = User.query.get(user_id)
    if not user:
        return jsonify({'ok': False, 'error': 'Usuário não encontrado'}), 404
    # Remove dados relacionados
    AccessToken.query.filter_by(user_id=user_id).delete()
    FollowerSnapshot.query.filter(
        FollowerSnapshot.token_id.in_(
            db.session.query(AccessToken.id).filter_by(user_id=user_id)
        )
    ).delete(synchronize_session=False)
    SpyFollowerSnapshot.query.filter_by(user_id=user_id).delete()
    for mp in MonitoredProfile.query.filter_by(user_id=user_id).all():
        ProfileCountSnapshot.query.filter_by(profile_id=mp.id).delete()
    MonitoredProfile.query.filter_by(user_id=user_id).delete()
    for sub in Subscription.query.filter_by(user_id=user_id).all():
        Payment.query.filter_by(subscription_id=sub.id).delete()
    Subscription.query.filter_by(user_id=user_id).delete()
    db.session.delete(user)
    db.session.commit()
    return jsonify({'ok': True})

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
        hiker_key = request.form.get('hiker_key', '').strip()
        try:
            if mp_token:
                Config.set('MP_ACCESS_TOKEN', mp_token)
            if base_url:
                Config.set('BASE_URL', base_url)
            if hiker_key:
                Config.set('HIKERAPI_KEY', hiker_key)
            db.session.commit()
            flash('Configurações salvas!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao salvar: {e}', 'error')
        return redirect(url_for('admin_settings'))

    mp_token_saved = Config.get('MP_ACCESS_TOKEN', '')
    base_url_saved = Config.get('BASE_URL', os.getenv('BASE_URL', ''))
    hiker_key_saved = Config.get('HIKERAPI_KEY', os.getenv('HIKERAPI_KEY', ''))
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
        hiker_key_saved=hiker_key_saved,
        hiker_configured=bool(hiker_key_saved),
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

# ─── API PREVIEW PÚBLICO (landing page teaser) ───────────────────────────────
@app.route('/api/preview/<username>')
def ig_preview(username):
    """Teaser público da landing: foto + contagem de um perfil do Instagram.
    Usa a fonte única _ig_profile (HikerAPI em produção)."""
    import re as _re
    username = username.strip().lstrip('@').lower()
    if not username or not _re.match(r'^[\w.]{1,30}$', username):
        return jsonify({'ok': False, 'error': 'username inválido'}), 400

    info = _ig_profile(username)
    if not info:
        return jsonify({'ok': False, 'username': username})

    return jsonify({
        'ok': True,
        'username': info['username'],
        'display_name': info['full_name'] or info['username'],
        'photo_url': info['photo_url'],
        'followers': info['followers'],
        'following': info['following'],
        'is_private': info['is_private'],
    })

# ─── API SPY VIA SESSÃO (extensão envia contagem de perfis monitorados) ───────
@app.route('/api/spy/update', methods=['POST'])
def spy_update():
    """Extensão envia contagem de seguidores de um perfil monitorado via sessão IG."""
    import json as _json
    data      = request.json or {}
    token_str = data.get('token', '')
    t = AccessToken.query.filter_by(token=token_str).first()
    if not t or not t.is_valid:
        return jsonify({'ok': False, 'error': 'Token inválido'}), 401

    username   = data.get('username', '').lower().strip().lstrip('@')
    followers  = data.get('followers')
    following  = data.get('following', 0)
    is_private = bool(data.get('is_private', False))
    profile_pic= data.get('profile_pic_url', '')
    full_name  = data.get('full_name', '')

    if not username or followers is None:
        return jsonify({'ok': False, 'error': 'username e followers são obrigatórios'}), 400

    # Busca ou cria MonitoredProfile para este usuário
    mp = MonitoredProfile.query.filter_by(user_id=t.user_id, username=username).first()
    if not mp:
        mp = MonitoredProfile(user_id=t.user_id, username=username)
        db.session.add(mp)
        db.session.flush()

    # Snapshot anterior para calcular diff
    prev = mp.snapshots.order_by(ProfileCountSnapshot.created_at.desc()).first()
    prev_followers = prev.followers if prev else None

    snap = ProfileCountSnapshot(
        profile_id=mp.id,
        followers=int(followers),
        following=int(following),
        is_private=is_private
    )
    db.session.add(snap)
    db.session.commit()

    diff = (int(followers) - prev_followers) if prev_followers is not None else None
    return jsonify({
        'ok': True,
        'username': username,
        'followers': int(followers),
        'following': int(following),
        'diff': diff,
        'prev_followers': prev_followers,
        'is_new': prev is None,
    })

# ─── API SPY FOLLOWER LIST (diff quem chegou/saiu) ───────────────────────────
@app.route('/api/spy/follower_snapshot', methods=['POST'])
def spy_follower_snapshot():
    """Salva lista completa de seguidores de um perfil espionado e retorna diff."""
    import json as _json
    data      = request.json or {}
    token_str = data.get('token', '')
    t = AccessToken.query.filter_by(token=token_str).first()
    if not t or not t.is_valid:
        return jsonify({'ok': False, 'error': 'Token inválido'}), 401

    username  = data.get('username', '').lower().strip().lstrip('@')
    followers = data.get('followers', [])   # lista de usernames

    if not username or not isinstance(followers, list):
        return jsonify({'ok': False, 'error': 'username e followers são obrigatórios'}), 400

    # Snapshot anterior
    prev = SpyFollowerSnapshot.query.filter_by(
        user_id=t.user_id, ig_username=username
    ).order_by(SpyFollowerSnapshot.created_at.desc()).first()

    prev_set = set(_json.loads(prev.followers)) if prev else set()
    curr_set = set(followers)

    # GUARDA ANTI-FANTASMA: se a lista anterior foi capturada truncada (ex: 331 de 502),
    # comparar com a atual inventa "saíram/chegaram" que são só ruído. Se a contagem entre
    # as duas capturas salta >15%, marca inconsistente e NÃO compara — salva a nova (boa)
    # captura pra virar a base correta da próxima vez.
    inconsistente = False
    joined = []
    left   = []
    if prev:
        a, b = len(curr_set), len(prev_set)
        if max(a, b) > max(min(a, b), 1) * 1.15:
            inconsistente = True
        else:
            joined = list(curr_set - prev_set)   # chegaram
            left   = list(prev_set - curr_set)   # saíram

    # Salva novo snapshot (mesmo se inconsistente: substitui a base ruim pela boa)
    snap = SpyFollowerSnapshot(
        user_id=t.user_id,
        ig_username=username,
        followers=_json.dumps(followers)
    )
    db.session.add(snap)
    db.session.commit()

    return jsonify({
        'ok': True,
        'is_new': prev is None,
        'inconsistente': inconsistente,
        'total': len(curr_set),
        'prev_total': len(prev_set),
        'joined': joined,
        'left': left,
    })

# ─── API EXTENSÃO ────────────────────────────────────────────────────────────
@app.route('/api/me')
def api_me():
    """Retorna o token do usuário logado via sessão do site (usado pela extensão para auto-login)."""
    user = _get_current_user()
    if not user:
        return jsonify({'ok': False, 'error': 'not_logged_in'}), 401
    # is_valid é @property (não coluna) — não pode ir no filter_by; filtra em Python
    token = AccessToken.query.filter_by(user_id=user.id).order_by(AccessToken.created_at.desc()).first()
    if not token or not token.is_valid:
        return jsonify({'ok': False, 'error': 'no_token'}), 403
    return jsonify({'ok': True, 'token': token.token, 'plan': token.plan})

@app.route('/api/auth/email', methods=['POST'])
def auth_by_email():
    """Extensão envia email do usuário → backend retorna token se assinatura ativa."""
    data  = request.json or {}
    email = data.get('email', '').strip().lower()
    if not email:
        return jsonify({'ok': False, 'error': 'Email obrigatório'}), 400

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({'ok': False, 'error': 'Email não encontrado. Verifique e tente novamente.'}), 404

    token = AccessToken.query.filter_by(user_id=user.id).order_by(AccessToken.created_at.desc()).first()
    if not token or not token.is_valid:
        return jsonify({'ok': False, 'error': 'Sem acesso ativo. Adquira um plano em foiembora.up.railway.app'}), 403

    return jsonify({'ok': True, 'token': token.token, 'plan': token.plan})

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
    inconsistente = False

    if prev:
        prev_followers = set(_json.loads(prev.followers))
        curr_followers = set(followers)
        # GUARDA: se a contagem saltou demais (>15%), captura anômala (sugeridos/extras
        # ou truncada). Não comparamos pra não gerar unfollowers fantasma.
        a, b = len(curr_followers), len(prev_followers)
        if max(a, b) > max(min(a, b), 1) * 1.15:
            inconsistente = True
        else:
            unfollowers    = list(prev_followers - curr_followers)   # pararam de seguir
            new_followers  = list(curr_followers - prev_followers)   # começaram a seguir
        prev_date      = br_dt(prev.created_at, '%d/%m/%Y')
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
        'inconsistente':  inconsistente,
        'prev_date':      prev_date,
        'total_snapshots': t.snapshots.count()
    })

@app.route('/api/snapshot/debug')
def snapshot_debug():
    """DIAGNÓSTICO (somente leitura): inspeciona os snapshots do usuário logado para
    descobrir de onde saem 'unfollowers' fantasma. Auth por sessão (igual minha-conta)."""
    import json as _json
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'login necessário'}), 401
    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({'error': 'usuário não encontrado'}), 404
    t = user.tokens.order_by(AccessToken.created_at.desc()).first()
    if not t:
        return jsonify({'error': 'sem token'}), 404

    # ordem cronológica (mais antigo -> mais novo)
    snaps = list(reversed(t.snapshots.order_by(FollowerSnapshot.created_at.desc()).limit(15).all()))

    rows_html = []
    for i, s in enumerate(snaps):
        fol = set(_json.loads(s.followers))
        flw = set(_json.loads(s.following))
        if i > 0:
            prev = snaps[i - 1]
            fol_prev = set(_json.loads(prev.followers))
            flw_prev = set(_json.loads(prev.following))
            unf = fol_prev - fol     # "parou de te seguir"
            new = fol - fol_prev     # "novo seguidor"
            n_unf = len(unf)
            n_seg = len(unf & flw)   # destes, quantos VOCÊ segue (smoking gun)
            pct = (100 * n_seg // n_unf) if n_unf else 0
            cor = '#fd6b6b' if pct >= 50 else ('#f59e0b' if pct >= 15 else '#22c55e')
            # amostra: nome + se você segue essa pessoa
            sample = []
            for u in sorted(unf)[:15]:
                segue = '👉 VOCÊ SEGUE' if u in flw else ''
                sample.append(f"@{u} <span style='color:#fd6b6b'>{segue}</span>")
            vs = (f"<b>{n_unf}</b> saíram · <b>{len(new)}</b> novos<br>"
                  f"<span style='color:{cor}'><b>{n_seg}</b> dos que 'saíram' são gente que VOCÊ segue ({pct}%)</span>"
                  f"<div style='font-size:11px;color:#a1a1aa;margin-top:6px'>{'<br>'.join(sample)}</div>")
        else:
            vs = '<span style="color:#71717a">— (primeira captura)</span>'
        rows_html.append(
            f"<tr><td>{i}</td><td>{br_dt(s.created_at)}</td><td><b>{len(fol)}</b></td>"
            f"<td>{len(flw)}</td><td>{len(fol & flw)}</td><td style='text-align:left'>{vs}</td></tr>"
        )

    html = f"""<!DOCTYPE html><html lang=pt-BR><head><meta charset=utf-8>
<meta name=viewport content='width=device-width,initial-scale=1'>
<title>Diagnóstico FoiEmbora</title>
<style>
body{{font-family:system-ui,sans-serif;background:#09090b;color:#f4f4f5;padding:16px;margin:0}}
h1{{font-size:18px}} p{{color:#a1a1aa;font-size:13px;line-height:1.5}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{border:1px solid #27272a;padding:8px;text-align:center;vertical-align:top}}
th{{background:#18181c;color:#a1a1aa;font-size:11px;text-transform:uppercase}}
.box{{background:#18181c;border:1px solid #27272a;border-radius:12px;padding:14px;margin-bottom:16px}}
</style></head><body>
<h1>🔬 Diagnóstico de seguidores</h1>
<div class=box><p><b>Plano:</b> {t.plan} · <b>Total de capturas:</b> {t.snapshots.count()}<br>
Coluna-chave: <b>"dos que saíram, quantos você segue"</b>. Se for alto (vermelho), o sistema está
misturando sua lista de <i>quem você segue</i> com <i>quem te segue</i> — é o bug.</p></div>
<table>
<thead><tr><th>#</th><th>Data (Brasília)</th><th>Seguidores<br>(lista)</th><th>Seguindo<br>(lista)</th>
<th>Você segue<br>E te segue</th><th>vs. captura anterior</th></tr></thead>
<tbody>{''.join(rows_html)}</tbody>
</table>
<p style='margin-top:16px'>📸 Tire um print desta tela inteira e mande pro Matheus/Claude.</p>
</body></html>"""
    from flask import Response
    return Response(html, mimetype='text/html')

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
        'date':      br_dt(s.created_at),
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

def _ig_profile(username):
    """FONTE ÚNICA de dados de perfil público do Instagram.
    Retorna {username, full_name, followers, following, is_private, photo_url} ou None.

    Ordem de tentativa (do mais confiável ao 'de graça'):
      1) HikerAPI  — proxy residencial gerenciado (100% mesmo em datacenter). Pago.
      2) ScraperAPI — proxy residencial rotativo (se configurado).
      3) Direto na API web_profile_info (só funciona em IP residencial).
      4) Scraping do HTML público.
    O servidor do Railway é datacenter, então sem (1) ou (2) o IG bloqueia tudo.
    """
    import re as _re
    username = (username or '').strip().lstrip('@').lower()
    if not username:
        return None

    hiker_key   = os.getenv('HIKERAPI_KEY')   or Config.get('HIKERAPI_KEY')
    scraper_key = os.getenv('SCRAPERAPI_KEY') or Config.get('SCRAPERAPI_KEY')

    def _norm(u):
        """Normaliza tanto o schema da HikerAPI quanto o do web_profile_info."""
        if not isinstance(u, dict):
            return None
        followers = u.get('follower_count')
        if followers is None and isinstance(u.get('edge_followed_by'), dict):
            followers = u['edge_followed_by'].get('count')
        following = u.get('following_count')
        if following is None and isinstance(u.get('edge_follow'), dict):
            following = u['edge_follow'].get('count')
        if followers is None:
            return None
        return {
            'username':   u.get('username') or username,
            'full_name':  u.get('full_name') or '',
            'followers':  int(followers),
            'following':  int(following or 0),
            'is_private': bool(u.get('is_private', False)),
            'photo_url':  u.get('profile_pic_url_hd') or u.get('profile_pic_url') or '',
        }

    h1 = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': '*/*', 'Accept-Language': 'pt-BR,pt;q=0.9',
        'x-ig-app-id': '936619743392459',
        'Referer': f'https://www.instagram.com/{username}/',
    }
    ig_url = f'https://i.instagram.com/api/v1/users/web_profile_info/?username={username}'

    # 1) HikerAPI — caminho confiável em produção (datacenter)
    if hiker_key:
        try:
            r = req_lib.get(
                'https://api.hikerapi.com/v1/user/by/username',
                params={'username': username},
                headers={'x-access-key': hiker_key, 'accept': 'application/json'},
                timeout=20,
            )
            if r.status_code == 200:
                d = r.json()
                u = d.get('user') if isinstance(d, dict) and 'user' in d else d
                res = _norm(u)
                if res:
                    return res
            else:
                app.logger.warning(f'[hikerapi] {username}: status {r.status_code}')
        except Exception as e:
            app.logger.warning(f'[hikerapi] {username}: {e}')

    # 2) ScraperAPI (proxy residencial rotativo)
    if scraper_key:
        try:
            proxy_url = (
                f'http://api.scraperapi.com?api_key={scraper_key}'
                f'&url={req_lib.utils.quote(ig_url, safe="")}'
                f'&render=false&country_code=br'
            )
            r = req_lib.get(proxy_url, headers=h1, timeout=30)
            if r.status_code == 200:
                res = _norm((r.json() or {}).get('data', {}).get('user'))
                if res:
                    return res
        except Exception as e:
            app.logger.warning(f'[scraperapi] {username}: {e}')

    # 3) Direto na API (só IP residencial)
    try:
        r = req_lib.get(ig_url, headers=h1, timeout=8)
        if r.status_code == 200:
            res = _norm((r.json() or {}).get('data', {}).get('user'))
            if res:
                return res
    except Exception:
        pass

    # 4) Scraping do HTML público
    try:
        page = req_lib.get(f'https://www.instagram.com/{username}/', headers=h1, timeout=8)
        m  = _re.search(r'"edge_followed_by":\{"count":(\d+)\}', page.text)
        m2 = _re.search(r'"edge_follow":\{"count":(\d+)\}', page.text)
        mn = _re.search(r'"full_name":"([^"]*)"', page.text)
        mp = _re.search(r'"profile_pic_url(?:_hd)?":"([^"]+)"', page.text)
        if m and m2:
            return {
                'username': username,
                'full_name': (mn.group(1) if mn else ''),
                'followers': int(m.group(1)),
                'following': int(m2.group(1)),
                'is_private': False,
                'photo_url': (mp.group(1).replace('\\u0026', '&').replace('\\/', '/') if mp else ''),
            }
    except Exception:
        pass

    return None


def _fetch_ig_counts(username):
    """Compat: alias para _ig_profile (mesmas chaves + extras)."""
    return _ig_profile(username)


def _hiker_user_id(username):
    """Resolve o @ -> (user_id, follower_count, is_private) via HikerAPI.
    Retorna (None, 0, False) se não achar/sem chave."""
    hiker_key = os.getenv('HIKERAPI_KEY') or Config.get('HIKERAPI_KEY')
    if not hiker_key:
        return None, 0, False
    username = (username or '').strip().lstrip('@').lower()
    try:
        r = req_lib.get(
            'https://api.hikerapi.com/v1/user/by/username',
            params={'username': username},
            headers={'x-access-key': hiker_key, 'accept': 'application/json'},
            timeout=20,
        )
        if r.status_code == 200:
            d = r.json()
            u = d.get('user') if isinstance(d, dict) and 'user' in d else d
            if isinstance(u, dict):
                pk = u.get('pk') or u.get('pk_id') or u.get('id')
                fc = u.get('follower_count') or 0
                pv = bool(u.get('is_private', False))
                return (str(pk) if pk else None), int(fc or 0), pv
        else:
            app.logger.warning(f'[hiker_user_id] {username}: status {r.status_code}')
    except Exception as e:
        app.logger.warning(f'[hiker_user_id] {username}: {e}')
    return None, 0, False


def _hiker_followers(user_id, max_pages=80):
    """Lista COMPLETA de usernames de seguidores via HikerAPI v2 (paginado por page_id).
    O navegador trava em ~333 pra contas de terceiros; aqui no servidor não tem esse teto.
    Retorna lista de usernames (lowercase). [] se falhar."""
    hiker_key = os.getenv('HIKERAPI_KEY') or Config.get('HIKERAPI_KEY')
    if not hiker_key:
        return []
    out, seen = [], set()
    page_id = None
    for _ in range(max_pages):
        params = {'user_id': str(user_id)}
        if page_id:
            params['page_id'] = page_id
        try:
            r = req_lib.get(
                'https://api.hikerapi.com/v2/user/followers',
                params=params,
                headers={'x-access-key': hiker_key, 'accept': 'application/json'},
                timeout=30,
            )
        except Exception as e:
            app.logger.warning(f'[hiker_followers] {user_id}: {e}')
            break
        if r.status_code != 200:
            app.logger.warning(f'[hiker_followers] {user_id}: status {r.status_code}')
            break
        d = r.json() or {}
        users = d.get('users')
        if users is None and isinstance(d.get('response'), dict):
            users = d['response'].get('users')
        for u in (users or []):
            un = (u.get('username') or '').lower() if isinstance(u, dict) else ''
            if un and un not in seen:
                seen.add(un)
                out.append(un)
        page_id = d.get('next_page_id') or (d.get('response') or {}).get('next_page_id')
        if not page_id:
            break
    return out


@app.route('/api/spy/audit', methods=['POST'])
def spy_audit():
    """Auditoria da lista de seguidores de TERCEIROS feita 100% no servidor via HikerAPI.
    O navegador (extensão) não passa de ~333 seguidores pra contas de terceiros — teto do
    Instagram. Aqui o servidor puxa a lista completa, salva snapshot e devolve o diff."""
    import json as _json
    data      = request.json or {}
    token_str = data.get('token', '')
    t = AccessToken.query.filter_by(token=token_str).first()
    if not t or not t.is_valid:
        return jsonify({'ok': False, 'error': 'Token inválido'}), 401

    username = data.get('username', '').lower().strip().lstrip('@')
    if not username:
        return jsonify({'ok': False, 'error': 'username é obrigatório'}), 400

    user_id, rep_count, is_private = _hiker_user_id(username)
    if not user_id:
        return jsonify({'ok': False, 'error': 'Perfil não encontrado.'}), 404
    if is_private:
        return jsonify({'ok': False, 'error': 'Perfil privado — não é possível auditar seguidores.'}), 400
    # Trava de custo: cada página da lista é uma chamada paga da HikerAPI. Perfis enormes
    # custariam caro e raramente são o alvo — pra esses, usar só o modo contagem.
    if rep_count and rep_count > 5000:
        return jsonify({'ok': False,
                        'error': f'Perfil grande demais para auditar a lista ({rep_count:,} seguidores). '
                                 f'Use o modo contagem para acompanhar este perfil.'.replace(',', '.')}), 400

    followers = _hiker_followers(user_id)
    n = len(set(followers))
    if n == 0:
        return jsonify({'ok': False, 'error': 'Não foi possível puxar a lista agora. Tente de novo em instantes.'}), 502
    # Guarda: se a HikerAPI parou no meio, não compara (senão inventa fantasma).
    if rep_count and n < rep_count * 0.85:
        return jsonify({'ok': False,
                        'error': f'Lista veio incompleta ({n} de ~{rep_count}). Tente de novo em instantes.'}), 502

    prev = SpyFollowerSnapshot.query.filter_by(
        user_id=t.user_id, ig_username=username
    ).order_by(SpyFollowerSnapshot.created_at.desc()).first()

    prev_set = set(_json.loads(prev.followers)) if prev else set()
    curr_set = set(followers)

    inconsistente = False
    joined, left = [], []
    if prev:
        a, b = len(curr_set), len(prev_set)
        if max(a, b) > max(min(a, b), 1) * 1.15:
            inconsistente = True
        else:
            joined = list(curr_set - prev_set)
            left   = list(prev_set - curr_set)

    snap = SpyFollowerSnapshot(
        user_id=t.user_id, ig_username=username,
        followers=_json.dumps(sorted(curr_set))
    )
    db.session.add(snap)
    db.session.commit()

    return jsonify({
        'ok': True,
        'is_new': prev is None,
        'inconsistente': inconsistente,
        'total': len(curr_set),
        'prev_total': len(prev_set),
        'joined': joined,
        'left': left,
    })


@app.route('/api/spy/audit_debug')
def spy_audit_debug():
    """DIAGNÓSTICO (auth por sessão do site). Chama a HikerAPI DUAS vezes para o mesmo @
    e mostra: contagem reportada, quanto cada chamada trouxe, e a diferença ENTRE as duas
    chamadas. Se a HikerAPI for estável, call1 ≈ call2 e a diferença é ~0. Se ela devolver
    listas incompletas que variam, a diferença explode — é a causa do 'tanto de seguidores'."""
    from flask import Response
    import traceback as _tb
    try:
        return _spy_audit_debug_impl()
    except Exception as e:
        return Response('<pre style="white-space:pre-wrap;color:#c0392b">ERRO:\n'
                        + str(e) + '\n\n' + _tb.format_exc() + '</pre>', mimetype='text/html')


def _spy_audit_debug_impl():
    from flask import Response
    user = _get_current_user()
    if not user:
        return Response('<h2>Faca login no painel primeiro: '
                        '<a href="/entrar">/entrar</a></h2>', mimetype='text/html')
    username = (request.args.get('username') or '').strip().lstrip('@').lower()
    if not username:
        return Response('<h2>Use ?username=perfil na URL</h2>', mimetype='text/html')

    user_id, rep_count, is_private = _hiker_user_id(username)
    if not user_id:
        return Response(f'<h2>@{username}: HikerAPI nao resolveu o user_id. '
                        f'Chave configurada? Perfil existe?</h2>', mimetype='text/html')

    call1 = _hiker_followers(user_id)
    call2 = _hiker_followers(user_id)
    s1, s2 = set(call1), set(call2)
    inter   = s1 & s2
    only1   = s1 - s2
    only2   = s2 - s1
    union   = s1 | s2

    def pct(n, d):
        return f'{(100.0 * n / d):.1f}%' if d else '—'

    rows = [
        ('Seguidores reportados (perfil)', rep_count),
        ('Chamada 1 — capturou', len(s1)),
        ('Chamada 2 — capturou', len(s2)),
        ('Em AMBAS (estável)', len(inter)),
        ('Só na chamada 1', len(only1)),
        ('Só na chamada 2', len(only2)),
        ('União das duas', len(union)),
        ('Estabilidade (ambas ÷ união)', pct(len(inter), len(union))),
        ('Completude média vs reportado', pct((len(s1) + len(s2)) // 2, rep_count)),
    ]
    estavel = bool(union) and len(inter) >= len(union) * 0.97
    veredito = (
        '✅ HikerAPI ESTÁVEL — a lista é confiável, o "tanto de seguidores" não vem daqui.'
        if estavel else
        '🔴 HikerAPI INSTÁVEL — cada chamada traz gente diferente. É ISSO que gera os '
        'seguidores fantasmas. A trava de 85% é frouxa demais; precisa exigir lista completa.'
    )
    color = 'green' if estavel else '#c0392b'

    sample1 = ', '.join(list(only1)[:15]) or '—'
    sample2 = ', '.join(list(only2)[:15]) or '—'
    body = ''.join(f'<tr><td style="padding:6px 12px;border:1px solid #ccc">{k}</td>'
                   f'<td style="padding:6px 12px;border:1px solid #ccc;font-weight:bold">{v}</td></tr>'
                   for k, v in rows)
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Audit debug @{username}</title></head>
<body style="font-family:system-ui;max-width:760px;margin:24px auto;padding:0 16px">
<h2>Diagnóstico HikerAPI — @{username}</h2>
<p style="font-size:18px;color:{color};font-weight:bold">{veredito}</p>
<table style="border-collapse:collapse;margin:16px 0">{body}</table>
<p><b>Só na chamada 1 (sumiram na 2):</b><br>{sample1}</p>
<p><b>Só na chamada 2 (não tinham na 1):</b><br>{sample2}</p>
</body></html>"""
    return Response(html, mimetype='text/html')


@app.route('/api/monitor/list')
def monitor_list():
    """Retorna lista atualizada de perfis monitorados (usada pelo auto-refresh do dashboard)."""
    user = _get_current_user()
    if not user:
        return jsonify({'ok': False}), 401
    result = []
    for mp in user.monitored_profiles.order_by(MonitoredProfile.created_at.desc()).all():
        snaps = mp.snapshots.order_by(ProfileCountSnapshot.created_at.desc()).limit(2).all()
        latest = snaps[0] if snaps else None
        prev   = snaps[1] if len(snaps) > 1 else None
        result.append({
            'username':   mp.username,
            'followers':  latest.followers if latest else 0,
            'following':  latest.following if latest else 0,
            'is_private': latest.is_private if latest else False,
            'diff':       (latest.followers - prev.followers) if (latest and prev) else None,
            'last_check': br_dt(latest.created_at, '%d/%m %H:%M') if latest else None,
        })
    return jsonify({'ok': True, 'profiles': result})

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

    # CAMINHO PRIMÁRIO (grátis): contagens da sessão logada do usuário (extensão/favorito)
    if client_followers is not None:
        info = {
            'username': username,
            'followers': int(client_followers),
            'following': int(client_following or 0),
            'is_private': False
        }
    else:
        # Fallback: busca no servidor via HikerAPI (_ig_profile)
        info = _ig_profile(username)

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

@app.route('/api/monitor/check/<username>', methods=['POST'])
def monitor_check(username):
    user = _get_current_user()
    if not user:
        return jsonify({'error': 'Não autenticado'}), 401
    username = username.lower().lstrip('@')
    profile = MonitoredProfile.query.filter_by(user_id=user.id, username=username).first()
    if not profile:
        return jsonify({'error': 'Perfil não monitorado'}), 404

    # Tenta buscar automaticamente primeiro
    info = _fetch_ig_counts(username)

    # Se falhou, aceita contagem manual enviada pelo frontend
    if not info:
        body = request.get_json(silent=True) or {}
        manual_followers = body.get('followers')
        manual_following = body.get('following')
        if manual_followers is not None:
            info = {
                'username': username,
                'followers': int(manual_followers),
                'following': int(manual_following or 0),
                'is_private': False,
            }

    if not info:
        return jsonify({'ok': False, 'blocked': True,
                        'message': 'Instagram bloqueou a busca automática. Insira a contagem manualmente.'}), 200

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
    history = [{'date': br_dt(s.created_at, '%d/%m %H:%M'), 'followers': s.followers, 'following': s.following} for s in snaps]
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
        # Mostra onboarding na primeira vez (ou se nunca fechou)
        if not session.get('onboarding_done'):
            return redirect(url_for('bem_vindo'))
        return redirect(url_for('minha_conta'))
    return render_template('user/login.html')

@app.route('/privacidade')
def privacidade():
    return render_template('privacidade.html')

@app.route('/bem-vindo')
def bem_vindo():
    if not session.get('user_email'):
        return redirect(url_for('user_login'))
    return render_template('user/onboarding.html')

@app.route('/bem-vindo/concluir', methods=['GET', 'POST'])
def bem_vindo_concluir():
    # Aceita GET também: se o usuário recarrega a página (F5) ou abre o link direto,
    # antes dava "Method Not Allowed" e travava o acesso ao painel. Agora só redireciona.
    session['onboarding_done'] = True
    return redirect(url_for('minha_conta'))

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
            inconsistente = False
            if i < len(snaps) - 1:
                prev = snaps[i + 1]
                followers_prev = set(_json.loads(prev.followers))
                # GUARDA: se a contagem saltou demais (>15%), a captura é anômala
                # (Instagram devolveu contas sugeridas/extras ou veio truncada).
                # Não comparamos — senão aparece "cacetada de gente" fantasma.
                a, b = len(followers_now), len(followers_prev)
                if max(a, b) > max(min(a, b), 1) * 1.15:
                    inconsistente = True
                else:
                    unfollowers   = sorted(followers_prev - followers_now)
                    new_followers = sorted(followers_now  - followers_prev)
            history.append({
                'date':          br_dt(s.created_at),
                'followers':     len(followers_now),
                'following':     len(following_now),
                'unfollowers':   unfollowers,
                'new_followers': new_followers,
                'inconsistente': inconsistente,
            })

    # Perfis monitorados (spy feature)
    monitored = []
    for mp in user.monitored_profiles.order_by(MonitoredProfile.created_at.desc()).all():
        snaps_mp = mp.snapshots.order_by(ProfileCountSnapshot.created_at.desc()).limit(30).all()
        snap_history = [{'date': br_dt(s.created_at, '%d/%m %H:%M'), 'followers': s.followers, 'following': s.following} for s in reversed(snaps_mp)]
        latest_snap = snaps_mp[0] if snaps_mp else None
        prev_snap   = snaps_mp[1] if len(snaps_mp) > 1 else None

        # Diff de QUEM entrou/saiu (lista completa de seguidores do perfil espionado)
        fsnaps = SpyFollowerSnapshot.query.filter_by(
            user_id=user.id, ig_username=mp.username.lower()
        ).order_by(SpyFollowerSnapshot.created_at.desc()).limit(30).all()
        follower_changes = []
        for i in range(len(fsnaps) - 1):
            curr = set(_json.loads(fsnaps[i].followers))
            prev = set(_json.loads(fsnaps[i + 1].followers))
            gained = sorted(curr - prev)
            lost   = sorted(prev - curr)
            if gained or lost:
                follower_changes.append({
                    'date':   br_dt(fsnaps[i].created_at),
                    'gained': gained,
                    'lost':   lost,
                })

        monitored.append({
            'username':   mp.username,
            'followers':  latest_snap.followers if latest_snap else 0,
            'following':  latest_snap.following if latest_snap else 0,
            'is_private': latest_snap.is_private if latest_snap else False,
            'diff':       (latest_snap.followers - prev_snap.followers) if (latest_snap and prev_snap) else None,
            'last_check': br_dt(latest_snap.created_at, '%d/%m %H:%M') if latest_snap else None,
            'history':    snap_history,
            'follower_changes': follower_changes,
            'has_follower_data': len(fsnaps) > 0,
        })

    sub = user.active_subscription
    plan_limit = 20 if (sub and sub.plan == 'trimestral') else 3

    # Gera bookmarklet personalizado com o token do usuário
    bm_token = t.token if t else ''
    bm_js = (
        "javascript:(function(){"
        f"var T='{bm_token}',A='https://foiembora.up.railway.app';"
        "function gc(n){var c=document.cookie.split(';');"
        "for(var i=0;i<c.length;i++){var p=c[i].trim().split('=');"
        "if(p[0]===n)return decodeURIComponent(p[1]||'');}return null;}"
        "var uid=gc('ds_user_id');"
        "if(!uid){alert('FoiEmbora: Faca login no Instagram primeiro!');return;}"
        "var H={'x-ig-app-id':'936619743392459','x-csrftoken':gc('csrftoken')||'','accept':'*/*','x-requested-with':'XMLHttpRequest'};"
        "async function pag(url){var l=[],nx=null;"
        "do{var r=await fetch(url+'?count=200'+(nx?'&max_id='+nx:''),{headers:H,credentials:'include'});"
        "var d=await r.json();l.push(...(d.users||[]));nx=d.next_max_id||null;"
        "if(nx)await new Promise(r=>setTimeout(r,600));}while(nx);return l;}"
        "var ov=document.createElement('div');"
        "ov.style='position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.88);z-index:999999;display:flex;align-items:center;justify-content:center;font-family:sans-serif';"
        "ov.innerHTML='<div style=\"background:#18181c;border-radius:16px;padding:24px;max-width:320px;width:90%;text-align:center;color:#fff\">"
        "<div style=\"font-size:1.1rem;font-weight:800;margin-bottom:8px\">FoiEmbora</div>"
        "<div id=fei-s style=\"font-size:0.85rem;color:#a1a1aa\">Carregando...</div></div>';"
        "document.body.appendChild(ov);"
        "var st=document.getElementById('fei-s');"
        "(async function(){try{"
        "st.textContent='Buscando seguidores...';"
        "var fw=await pag('/api/v1/friendships/'+uid+'/following/');"
        "st.textContent='Buscando '+fw.length+' seguidos...';"
        "var fl=await pag('/api/v1/friendships/'+uid+'/followers/');"
        "st.textContent='Salvando...';"
        "var r=await fetch(A+'/api/snapshot',{method:'POST',headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({token:T,followers:fl.map(u=>u.username),following:fw.map(u=>u.username)})});"
        "var d=await r.json();"
        "if(d.ok){var m='<div style=\"font-size:1rem;font-weight:800;margin-bottom:10px\">FoiEmbora</div>';"
        "if(d.unfollowers&&d.unfollowers.length){"
        "m+='<div style=\"font-size:0.75rem;color:#f87171;font-weight:700;margin-bottom:4px\">Pararam de te seguir:</div>';"
        "d.unfollowers.forEach(u=>{m+='<div style=\"font-size:0.8rem;color:#fff\">@'+u+'</div>';});"
        "m+='<br>';}else{m+='<div style=\"color:#4ade80;font-size:0.8rem;margin-bottom:8px\">Ninguem parou de te seguir</div>';}"
        "if(d.new_followers&&d.new_followers.length){"
        "m+='<div style=\"font-size:0.75rem;color:#4ade80;font-weight:700;margin-bottom:4px\">Novos:</div>';"
        "d.new_followers.forEach(u=>{m+='<div style=\"font-size:0.8rem;color:#fff\">@'+u+'</div>';});}"
        "m+='<br><button onclick=\"this.closest(\\\"[style*=fixed]\\\").remove()\" style=\"background:linear-gradient(135deg,#833ab4,#fd1d1d,#fcb045);border:none;color:#fff;padding:8px 20px;border-radius:8px;font-weight:700;cursor:pointer\">Fechar</button>';"
        "ov.querySelector('div').innerHTML=m;}"
        "else{ov.querySelector('div').innerHTML='<p style=\"color:#f87171\">'+d.error+'</p><button onclick=\"this.closest(\\\"[style*=fixed]\\\").remove()\" style=\"padding:8px 16px;border-radius:8px;background:#333;color:#fff;border:none;cursor:pointer;margin-top:8px\">Fechar</button>';}"
        "}catch(e){ov.querySelector('div').innerHTML='<p style=\"color:#f87171\">'+e.message+'</p><button onclick=\"this.closest(\\\"[style*=fixed]\\\").remove()\" style=\"padding:8px 16px;border-radius:8px;background:#333;color:#fff;border:none;cursor:pointer;margin-top:8px\">Fechar</button>';}})();"
        "})();"
    )

    return render_template('user/dashboard.html',
        token=t,
        bookmarklet=bm_js,
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
