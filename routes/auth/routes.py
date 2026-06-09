
from app_core import *
import random
import threading



def generate_6_digit_otp():
    return f"{random.randint(0, 999999):06d}"


def send_registration_otp_email(email, otp, name=""):
    subject = "Verify your NE-Fresh account"

    html = f"""\
<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>NE-Fresh OTP</title>
</head>
<body style="margin:0;padding:0;background:#F8FFFB;font-family:Arial,Helvetica,sans-serif;color:#23332A;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F8FFFB;padding:28px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;background:#FFFFFF;border:1px solid rgba(0,168,89,0.16);border-radius:28px;overflow:hidden;box-shadow:0 18px 44px rgba(17,64,38,0.09);">
          <tr>
            <td style="padding:0;height:6px;background:linear-gradient(90deg,#00A859,#008A48,#6DDFA5);"></td>
          </tr>

          <tr>
            <td style="padding:34px 34px 18px 34px;text-align:center;background:linear-gradient(180deg,#FFFFFF 0%,#FBFFFD 100%);">
              <div style="display:inline-block;padding:10px 18px;border-radius:999px;border:1px solid #DDF4E8;background:#F6FFF9;color:#008A48;font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;">
                Secure Email Verification
              </div>

              <h1 style="margin:18px 0 0 0;font-size:32px;line-height:1.05;font-weight:900;letter-spacing:-0.04em;color:#23332A;">
                Verify your <span style="color:#00A859;">email address</span>
              </h1>

              <p style="margin:14px auto 0 auto;max-width:500px;font-size:15px;line-height:1.7;color:#66756D;">
                Hi {name or 'there'}, we have sent a one-time password to your registered email address. Enter the 6-digit code below to activate your account.
              </p>
            </td>
          </tr>

          <tr>
            <td style="padding:10px 34px 0 34px;">
              <div style="padding:16px 16px;border-radius:18px;border:1px solid #DDF4E8;background:#F8FFFB;">
                <div style="font-size:12px;font-weight:700;color:#8A9890;line-height:1.2;">Verification code</div>
                <div style="margin-top:6px;font-size:34px;font-weight:900;color:#00A859;letter-spacing:.22em;line-height:1.2;text-align:center;">
                  {otp}
                </div>
              </div>

              <div style="margin-top:12px;padding:16px 16px;border-radius:18px;background:#FFF7E8;border:1px solid rgba(255,184,77,0.28);color:#8A5A00;font-size:13px;line-height:1.6;font-weight:600;">
                This code is valid for <strong>2 minutes</strong>. Do not share it with anyone. If it expires, request a new one after 1 minute.
              </div>
            </td>
          </tr>

          <tr>
            <td style="padding:6px 34px 32px 34px;">
              <div style="margin-top:8px;padding:16px 18px;border-radius:18px;background:#F0FFF7;border:1px solid rgba(0,168,89,0.16);color:#40584E;font-size:13px;line-height:1.6;">
                If you did not create this account, you can ignore this email safely.
              </div>

              <p style="margin:18px 0 0 0;font-size:12px;line-height:1.6;color:#8A9890;text-align:center;">
                NE-Fresh Team
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""

    try:
        mail_sender = globals().get("send_email")
        if callable(mail_sender):
            mail_sender(email, subject, html)
            print(f"[OTP SENT] {email}")
            return
    except Exception as e:
        print(f"[OTP EMAIL ERROR] {str(e)}")
        raise

    print(f"[DEV OTP EMAIL] To: {email} | Subject: {subject} | OTP: {otp}")





@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email','').lower().strip()
        password = request.form.get('password','')

        u = mongo.users.find_one({"email": email})

        if not u:
            flash('Invalid credentials.', 'danger')
            return redirect(url_for('login'))

        if not u.get('is_active') and u.get('role') != 'customer':
            flash('Your account awaits admin approval.', 'warning')
            return redirect(url_for('login'))

        if check_password_hash(u.get('password_hash', ''), password):
            if u.get('role') == 'customer' and not u.get('email_verified', 1):
                flash('Please verify your email before logging in.', 'warning')
                return redirect(url_for('login'))

            session['user_id'] = str(u['_id'])
            flash('Welcome back!', 'success')

            if u.get('role') == 'admin':
                return redirect(url_for('admin_dashboard'))
            elif u.get('role') == 'store':
                return redirect(url_for('store_dashboard'))
            elif u.get('role') == 'delivery':
                return redirect(url_for('delivery_dashboard'))
            else:
                return redirect(url_for('index'))

        flash('Invalid credentials.', 'danger')

    return render_template('login.html')



def send_password_reset_email(email, name, reset_link):
    subject = "Reset your NE-Fresh password"

    html = f"""\
<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>NE-Fresh Password Reset</title>
</head>
<body style="margin:0;padding:0;background:#F8FFFB;font-family:Arial,Helvetica,sans-serif;color:#23332A;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F8FFFB;padding:28px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;background:#FFFFFF;border:1px solid rgba(0,168,89,0.16);border-radius:28px;overflow:hidden;box-shadow:0 18px 44px rgba(17,64,38,0.09);">
          <tr>
            <td style="padding:0;height:6px;background:linear-gradient(90deg,#00A859,#008A48,#6DDFA5);"></td>
          </tr>

          <tr>
            <td style="padding:34px 34px 18px 34px;text-align:center;background:linear-gradient(180deg,#FFFFFF 0%,#FBFFFD 100%);">
              <div style="display:inline-block;padding:10px 18px;border-radius:999px;border:1px solid #DDF4E8;background:#F6FFF9;color:#008A48;font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;">
                Password Reset
              </div>

              <h1 style="margin:18px 0 0 0;font-size:32px;line-height:1.05;font-weight:900;letter-spacing:-0.04em;color:#23332A;">
                Reset your <span style="color:#00A859;">password</span>
              </h1>

              <p style="margin:14px auto 0 auto;max-width:500px;font-size:15px;line-height:1.7;color:#66756D;">
                Hi {name or 'there'}, we received a request to reset your NE-Fresh password. Click the button below to continue.
              </p>
            </td>
          </tr>

          <tr>
            <td style="padding:10px 34px 0 34px;">
              <div style="text-align:center;">
                <a href="{reset_link}" style="display:inline-block;background:#00A859;color:#fff;text-decoration:none;font-weight:900;font-size:15px;padding:14px 24px;border-radius:14px;box-shadow:0 14px 28px rgba(0,168,89,0.22);">
                  Reset Password
                </a>
              </div>

              <div style="margin-top:12px;padding:16px 16px;border-radius:18px;background:#FFF7E8;border:1px solid rgba(255,184,77,0.28);color:#8A5A00;font-size:13px;line-height:1.6;font-weight:600;">
                This reset link is valid for <strong>30 minutes</strong>. If you did not request this, you can safely ignore this email.
              </div>

              <div style="margin-top:12px;padding:16px 16px;border-radius:18px;background:#F8FFFB;border:1px solid #DDF4E8;color:#40584E;font-size:12px;line-height:1.6;word-break:break-all;">
                <strong style="display:block;margin-bottom:6px;">Direct link:</strong>
                {reset_link}
              </div>
            </td>
          </tr>

          <tr>
            <td style="padding:6px 34px 32px 34px;">
              <p style="margin:18px 0 0 0;font-size:12px;line-height:1.6;color:#8A9890;text-align:center;">
                NE-Fresh Team
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""

    mail_sender = globals().get("send_email")
    if callable(mail_sender):
        mail_sender(email, subject, html)
        return

    print(f"[DEV RESET LINK] Send this to the user: {reset_link}")







@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip().lower()

        u = mongo.users.find_one({
            "$or": [
                {"email": identifier},
                {"phone": identifier}
            ]
        })

        if u:
            token = secrets.token_urlsafe(32)
            now = datetime.utcnow()
            expires_at = (now + timedelta(minutes=30)).isoformat()

            mongo.password_reset_tokens.update_many(
                {
                    "user_id": str(u["_id"]),
                    "consumed": 0
                },
                {
                    "$set": {
                        "consumed": 1,
                        "consumed_at": now.isoformat()
                    }
                }
            )

            mongo.password_reset_tokens.insert_one({
                "user_id": str(u["_id"]),
                "token": token,
                "expires_at": expires_at,
                "consumed": 0,
                "created_at": now.isoformat()
            })

            reset_link = url_for('reset_password', token=token, _external=True)

            email_to_send = u.get('email', '')
            if email_to_send:
                threading.Thread(
                    target=send_password_reset_email,
                    args=(email_to_send, u.get('name', ''), reset_link),
                    daemon=True
                ).start()
            else:
                print(f"[DEV RESET LINK] Send this to the user: {reset_link}")

        flash("If the account exists, a reset link has been sent.", "info")
        return redirect(url_for('login'))

    return render_template('forgot_password.html')





@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    row = mongo.password_reset_tokens.find_one({
        "token": token,
        "consumed": 0
    })

    if not row:
        flash("Invalid or expired reset link.", "danger")
        return redirect(url_for('forgot_password'))

    try:
        if datetime.fromisoformat(row.get("expires_at")) < datetime.utcnow():
            flash("Invalid or expired reset link.", "danger")
            return redirect(url_for('forgot_password'))
    except Exception:
        flash("Invalid or expired reset link.", "danger")
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        new_pw = request.form.get('password', '')
        confirm = request.form.get('confirm', '') or request.form.get('confirm_password', '')

        if not new_pw or len(new_pw) < 6:
            flash("Password must be at least 6 characters.", "warning")
            return redirect(url_for('reset_password', token=token))

        if new_pw != confirm:
            flash("Passwords do not match.", "warning")
            return redirect(url_for('reset_password', token=token))

        pwd_hash = generate_password_hash(new_pw)

        try:
            user_obj_id = ObjectId(row.get("user_id"))
        except Exception:
            flash("Invalid or expired reset link.", "danger")
            return redirect(url_for('forgot_password'))

        mongo.users.update_one(
            {"_id": user_obj_id},
            {"$set": {"password_hash": pwd_hash}}
        )

        mongo.password_reset_tokens.update_one(
            {"_id": row["_id"]},
            {"$set": {"consumed": 1, "consumed_at": datetime.utcnow().isoformat()}}
        )

        flash("Your password has been reset. Please log in.", "success")
        return redirect(url_for('login'))

    return render_template('reset_password.html')



@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        name = (request.form.get('name','') or '').strip()
        email = (request.form.get('email','') or '').lower().strip()
        phone = (request.form.get('phone','') or '').strip()
        password = request.form.get('password','') or ''

        if not name or not email or not password:
            flash('Please fill all required fields.', 'warning')
            return redirect(url_for('register'))

        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'warning')
            return redirect(url_for('register'))

        if phone:
            phone = normalize_phone(phone)

        existing = mongo.users.find_one({
            "$or": [
                {"email": email},
                *([{"phone": phone}] if phone else [])
            ]
        })

        otp = generate_6_digit_otp()
        now = datetime.utcnow()
        otp_expires_at = (now + timedelta(minutes=2)).isoformat()
        otp_resend_after = (now + timedelta(minutes=1)).isoformat()

        user_payload = {
            "name": name,
            "email": email,
            "phone": phone,
            "password_hash": generate_password_hash(password),
            "role": "customer",
            "phone_verified": 1,
            "email_verified": 0,
            "is_active": 0,
            "otp_code": otp,
            "otp_expires_at": otp_expires_at,
            "otp_resend_after": otp_resend_after,
            "otp_attempts": 0,
            "created_at": now.isoformat()
        }

        try:
            if existing:
                if existing.get("email_verified", 1) == 1:
                    flash('Email or phone already registered.', 'danger')
                    return redirect(url_for('register'))

                mongo.users.update_one(
                    {"_id": existing["_id"]},
                    {"$set": user_payload}
                )
                user_id = str(existing["_id"])
            else:
                result = mongo.users.insert_one(user_payload)
                user_id = str(result.inserted_id)

        except DuplicateKeyError:
            flash('Email or phone already registered.', 'danger')
            return redirect(url_for('register'))

        session['pending_verification_user_id'] = user_id

        threading.Thread(
            target=send_registration_otp_email,
            args=(email, otp, name),
            daemon=True
        ).start()

        flash(
            'Account created. We sent a verification code to your email.',
            'success'
        )

        return redirect(
            url_for(
                'verify_email',
                user_id=user_id
            )
        )

    return render_template('register.html')





@app.route('/verify-email/<user_id>', methods=['GET'])
def verify_email(user_id):
    try:
        user = mongo.users.find_one({
            "_id": ObjectId(user_id)
        })
    except Exception:
        user = None

    if not user:
        flash('Verification request not found.', 'danger')
        return redirect(url_for('register'))

    if user.get('email_verified'):
        flash('Email already verified. Please login.', 'info')
        return redirect(url_for('login'))

    return render_template(
        'verify_email.html',
        user=user
    )



@app.route('/verify-email/<user_id>', methods=['POST'])
def verify_email_post(user_id):

    otp = (request.form.get('otp') or '').strip()

    try:
        user = mongo.users.find_one({
            "_id": ObjectId(user_id)
        })
    except Exception:
        user = None

    if not user:
        flash('Verification request not found.', 'danger')
        return redirect(url_for('register'))

    if user.get('email_verified'):
        flash('Email already verified.', 'info')
        return redirect(url_for('login'))

    try:
        expires_at = datetime.fromisoformat(
            user.get('otp_expires_at')
        )
    except Exception:
        flash('OTP expired. Request a new one.', 'danger')
        return redirect(
            url_for(
                'verify_email',
                user_id=user_id
            )
        )

    if datetime.utcnow() > expires_at:
        flash('OTP has expired.', 'danger')
        return redirect(
            url_for(
                'verify_email',
                user_id=user_id
            )
        )

    if user.get('otp_attempts', 0) >= 5:
        flash(
            'Too many incorrect attempts. Please resend OTP.',
            'danger'
        )
        return redirect(
            url_for(
                'verify_email',
                user_id=user_id
            )
        )

    if otp != str(user.get('otp_code', '')):
        mongo.users.update_one(
            {"_id": user["_id"]},
            {
                "$inc": {
                    "otp_attempts": 1
                }
            }
        )

        flash('Invalid OTP.', 'danger')

        return redirect(
            url_for(
                'verify_email',
                user_id=user_id
            )
        )

    mongo.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "email_verified": 1,
                "is_active": 1,
                "verified_at": datetime.utcnow().isoformat()
            },
            "$unset": {
                "otp_code": "",
                "otp_expires_at": "",
                "otp_resend_after": "",
                "otp_attempts": ""
            }
        }
    )

    session['user_id'] = str(user['_id'])

    flash(
        'Email verified successfully.',
        'success'
    )

    return redirect(url_for('index'))




@app.route('/resend-otp/<user_id>', methods=['POST'])
def resend_otp(user_id):

    try:
        user = mongo.users.find_one({
            "_id": ObjectId(user_id)
        })
    except Exception:
        user = None

    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('register'))

    if user.get('email_verified'):
        flash('Email already verified.', 'info')
        return redirect(url_for('login'))

    try:
        resend_after = datetime.fromisoformat(
            user.get('otp_resend_after')
        )
    except Exception:
        resend_after = datetime.utcnow()

    if datetime.utcnow() < resend_after:
        flash(
            'Please wait before requesting another OTP.',
            'warning'
        )

        return redirect(
            url_for(
                'verify_email',
                user_id=user_id
            )
        )

    otp = generate_6_digit_otp()

    now = datetime.utcnow()

    mongo.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "otp_code": otp,
                "otp_expires_at": (
                    now + timedelta(minutes=2)
                ).isoformat(),
                "otp_resend_after": (
                    now + timedelta(minutes=1)
                ).isoformat(),
                "otp_attempts": 0
            }
        }
    )

    send_registration_otp_email(
        user["email"],
        otp,
        user.get("name")
    )

    flash(
        'A new OTP has been sent.',
        'success'
    )

    return redirect(
        url_for(
            'verify_email',
            user_id=user_id
        )
    )



@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out.','info')
    return redirect(url_for('index'))

@app.route('/api/auth/login', methods=['POST'])
def api_auth_login():
    data = request.get_json(silent=True) or {}

    email = (data.get('email') or '').lower().strip()
    password = data.get('password') or ''

    if not email or not password:
        return jsonify({
            'success': False,
            'error': 'Email and password are required'
        }), 400

    u = mongo.users.find_one({"email": email})

    if not u:
        return jsonify({
            'success': False,
            'error': 'Invalid credentials'
        }), 401

    if not check_password_hash(u.get('password_hash', ''), password):
        return jsonify({
            'success': False,
            'error': 'Invalid credentials'
        }), 401

    if not u.get('is_active'):
        return jsonify({
            'success': False,
            'error': 'Account is inactive'
        }), 403

    user_id = str(u['_id'])
    token = generate_session_token(user_id)
    now = datetime.utcnow().isoformat()
    expires_at = (datetime.utcnow() + timedelta(days=30)).isoformat()

    mongo.api_sessions.insert_one({
        "user_id": user_id,
        "token": token,
        "created_at": now,
        "expires_at": expires_at
    })

    return jsonify({
        'success': True,
        'token': token,
        'user': {
            'id': user_id,
            'name': u.get('name', ''),
            'email': u.get('email', ''),
            'phone': u.get('phone', ''),
            'role': u.get('role', '')
        }
    })

@app.route('/api/auth/register', methods=['POST'])
def api_auth_register():
    data = request.get_json(silent=True) or {}

    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').lower().strip()
    phone = (data.get('phone') or '').strip()
    password = data.get('password') or ''

    if not name or not email or not password:
        return jsonify({
            'success': False,
            'error': 'Missing required fields'
        }), 400

    if len(password) < 6:
        return jsonify({
            'success': False,
            'error': 'Password must be at least 6 characters'
        }), 400

    if phone:
        phone = normalize_phone(phone)

    try:
        result = mongo.users.insert_one({
            "name": name,
            "email": email,
            "phone": phone,
            "password_hash": generate_password_hash(password),
            "role": "customer",
            "phone_verified": 1,
            "is_active": 1,
            "created_at": datetime.utcnow().isoformat()
        })
    except DuplicateKeyError:
        return jsonify({
            'success': False,
            'error': 'Email or phone already registered'
        }), 409
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

    user_id = str(result.inserted_id)
    token = generate_session_token(user_id)
    now = datetime.utcnow().isoformat()
    expires_at = (datetime.utcnow() + timedelta(days=30)).isoformat()

    mongo.api_sessions.insert_one({
        "user_id": user_id,
        "token": token,
        "created_at": now,
        "expires_at": expires_at
    })

    return jsonify({
        'success': True,
        'token': token,
        'user': {
            'id': user_id,
            'name': name,
            'email': email,
            'phone': phone,
            'role': 'customer'
        }
    })

@app.route('/api/auth/logout', methods=['POST'])
@api_login_required
def api_auth_logout(user_id):
    auth_header = request.headers.get('Authorization', '')
    token = auth_header.replace('Bearer ', '').strip()

    if token:
        mongo.api_sessions.delete_one({
            "token": token,
            "user_id": str(user_id)
        })

    return jsonify({'success': True})
