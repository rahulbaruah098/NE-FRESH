"""Auth routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *


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
            now = datetime.utcnow().isoformat()
            expires_at = (datetime.utcnow() + timedelta(minutes=30)).isoformat()

            mongo.password_reset_tokens.insert_one({
                "user_id": str(u["_id"]),
                "token": token,
                "expires_at": expires_at,
                "consumed": 0,
                "created_at": now
            })

            reset_link = url_for('reset_password', token=token, _external=True)
            print(f"[DEV RESET LINK] Send this to the user: {reset_link}")

            if u.get('phone'):
                try:
                    send_sms(u['phone'], f"Reset your password: {reset_link}")
                except Exception:
                    pass

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
        confirm = request.form.get('confirm', '')

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
            flash('Email or phone already registered.', 'danger')
            return redirect(url_for('register'))

        session['user_id'] = str(result.inserted_id)
        flash('Account created! You are logged in.', 'success')
        return redirect(url_for('index'))

    return render_template('register.html')

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
