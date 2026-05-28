from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone
import secrets, string

db = SQLAlchemy()

def now():
    return datetime.now(timezone.utc)

def gen_token(length=32):
    return secrets.token_urlsafe(length)

def gen_code(length=8):
    chars = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))


class Admin(UserMixin, db.Model):
    __tablename__ = 'admins'
    id           = db.Column(db.Integer, primary_key=True)
    email        = db.Column(db.String(255), unique=True, nullable=False)
    password_hash= db.Column(db.String(255), nullable=False)
    created_at   = db.Column(db.DateTime(timezone=True), default=now)

    def set_password(self, pw): self.password_hash = generate_password_hash(pw)
    def check_password(self, pw): return check_password_hash(self.password_hash, pw)
    def get_id(self): return f'admin:{self.id}'


class Seller(UserMixin, db.Model):
    __tablename__ = 'sellers'
    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(255), nullable=False)
    email           = db.Column(db.String(255), unique=True, nullable=False)
    password_hash   = db.Column(db.String(255), nullable=False)
    referral_code   = db.Column(db.String(20), unique=True, default=gen_code)
    commission_rate = db.Column(db.Numeric(5,2), default=30.00)
    is_active       = db.Column(db.Boolean, default=True)
    created_at      = db.Column(db.DateTime(timezone=True), default=now)

    subscriptions   = db.relationship('Subscription', backref='seller', lazy='dynamic')
    commissions     = db.relationship('Commission', backref='seller', lazy='dynamic')

    def set_password(self, pw): self.password_hash = generate_password_hash(pw)
    def check_password(self, pw): return check_password_hash(self.password_hash, pw)
    def get_id(self): return f'seller:{self.id}'

    @property
    def total_sales(self):
        return self.subscriptions.join(Payment).filter(Payment.status == 'approved').count()

    @property
    def total_commission(self):
        result = db.session.query(db.func.sum(Commission.amount))\
            .filter(Commission.seller_id == self.id).scalar()
        return float(result or 0)

    @property
    def pending_commission(self):
        result = db.session.query(db.func.sum(Commission.amount))\
            .filter(Commission.seller_id == self.id, Commission.status == 'pending').scalar()
        return float(result or 0)


class User(db.Model):
    __tablename__ = 'users'
    id          = db.Column(db.Integer, primary_key=True)
    email       = db.Column(db.String(255), unique=True, nullable=False)
    seller_id   = db.Column(db.Integer, db.ForeignKey('sellers.id'), nullable=True)
    created_at  = db.Column(db.DateTime(timezone=True), default=now)

    subscriptions = db.relationship('Subscription', backref='user', lazy='dynamic')
    tokens        = db.relationship('AccessToken', backref='user', lazy='dynamic')

    @property
    def active_subscription(self):
        return self.subscriptions.filter(
            Subscription.status == 'active',
            Subscription.expires_at > now()
        ).first()


class Subscription(db.Model):
    __tablename__ = 'subscriptions'
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    seller_id   = db.Column(db.Integer, db.ForeignKey('sellers.id'), nullable=True)
    plan        = db.Column(db.String(50), nullable=False)   # 'avulso' | 'trimestral'
    status      = db.Column(db.String(50), default='pending') # pending|active|expired|cancelled
    amount      = db.Column(db.Numeric(10,2), nullable=False)
    starts_at   = db.Column(db.DateTime(timezone=True), nullable=True)
    expires_at  = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at  = db.Column(db.DateTime(timezone=True), default=now)

    payments    = db.relationship('Payment', backref='subscription', lazy='dynamic')


class Payment(db.Model):
    __tablename__ = 'payments'
    id              = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey('subscriptions.id'), nullable=False)
    amount          = db.Column(db.Numeric(10,2), nullable=False)
    method          = db.Column(db.String(50))   # 'pix' | 'card'
    status          = db.Column(db.String(50), default='pending')  # pending|approved|failed
    mp_payment_id   = db.Column(db.String(255), nullable=True)
    pix_code        = db.Column(db.Text, nullable=True)
    created_at      = db.Column(db.DateTime(timezone=True), default=now)

    commission      = db.relationship('Commission', backref='payment', uselist=False)


class AccessToken(db.Model):
    __tablename__ = 'access_tokens'
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    token       = db.Column(db.String(255), unique=True, default=gen_token)
    plan        = db.Column(db.String(50), nullable=False)
    expires_at  = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at  = db.Column(db.DateTime(timezone=True), default=now)

    @property
    def is_valid(self):
        if self.expires_at is None:
            return True
        return now() < self.expires_at


class Config(db.Model):
    __tablename__ = 'config'
    key   = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text, nullable=True)

    @classmethod
    def get(cls, key, default=None):
        row = cls.query.get(key)
        return row.value if row else default

    @classmethod
    def set(cls, key, value):
        row = cls.query.get(key)
        if row:
            row.value = value
        else:
            db.session.add(cls(key=key, value=value))


class FollowerSnapshot(db.Model):
    """Histórico de seguidores para detectar quem parou de seguir — plano trimestral."""
    __tablename__ = 'follower_snapshots'
    id          = db.Column(db.Integer, primary_key=True)
    token_id    = db.Column(db.Integer, db.ForeignKey('access_tokens.id'), nullable=False)
    followers   = db.Column(db.Text, nullable=False)   # JSON list of usernames
    following   = db.Column(db.Text, nullable=False)   # JSON list of usernames
    created_at  = db.Column(db.DateTime(timezone=True), default=now)

    token = db.relationship('AccessToken', backref=db.backref('snapshots', lazy='dynamic'))


class Commission(db.Model):
    __tablename__ = 'commissions'
    id          = db.Column(db.Integer, primary_key=True)
    seller_id   = db.Column(db.Integer, db.ForeignKey('sellers.id'), nullable=False)
    payment_id  = db.Column(db.Integer, db.ForeignKey('payments.id'), nullable=False)
    amount      = db.Column(db.Numeric(10,2), nullable=False)
    status      = db.Column(db.String(50), default='pending')  # pending | paid
    paid_at     = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at  = db.Column(db.DateTime(timezone=True), default=now)
