# 🚀 Nexora Cloud - Bot Hosting Platform

## 📋 Overview
Nexora Cloud is a complete bot hosting platform with Firebase authentication, file management, and real-time bot monitoring.

## ✅ Latest Fixes (v9-v7-logout-fixed)

### 🔒 Critical Logout Fix
- **Added Firebase `signOut()`** to all logout functions
- **Fixed "Continue with Google"** authentication flow
- **Cleared Firebase IndexedDB** to prevent stale OAuth state
- **Proper 5-step logout** process implemented

See [LOGOUT_FIX_README.md](./LOGOUT_FIX_README.md) for detailed documentation.

---

## 🏗️ Project Structure

```
nexora-cloud-fixed/
├── app.py                    # Flask backend (FastAPI-style routes)
├── requirements.txt          # Python dependencies
├── vercel.json              # Vercel deployment config
├── templates/               # HTML templates
│   ├── index.html          # Login page (Firebase auth)
│   ├── dashboard.html      # Main dashboard
│   ├── bots.html           # Bot manager
│   ├── file_manager.html   # File manager
│   ├── admin_panel.html    # Admin panel
│   ├── settings.html       # User settings
│   ├── activity.html       # Activity logs
│   ├── subusers.html       # Sub-user management
│   ├── support.html        # Support tickets
│   └── ...                 # Other templates
├── static/                  # Static assets
│   ├── icon-192.png
│   └── icon-512.png
├── routes/                  # (Optional) Route modules
└── api/                     # (Optional) API modules
```

---

## 🔧 Setup Instructions

### 1. Prerequisites
- Python 3.9+
- Firebase project with Authentication enabled
- Firebase Realtime Database
- Vercel account (for deployment)

### 2. Firebase Setup
1. Create a Firebase project at https://console.firebase.google.com
2. Enable **Google Authentication**
3. Enable **Realtime Database**
4. Download service account JSON key
5. Copy Firebase config from project settings

### 3. Environment Variables

Create `.env` file (for local testing):
```env
FIREBASE_SA_JSON={"type":"service_account","project_id":"..."}
FIREBASE_WEB_API_KEY=your_web_api_key
FLASK_SECRET_KEY=your_secret_key
```

For Vercel deployment, set these in project settings.

### 4. Local Development
```bash
# Install dependencies
pip install -r requirements.txt

# Run server
python app.py

# Visit http://localhost:5000
```

### 5. Vercel Deployment
```bash
# Install Vercel CLI
npm i -g vercel

# Deploy
vercel --prod
```

---

## 🔐 Authentication Flow

### Login Process:
1. User clicks "Continue with Google"
2. Firebase `signInWithPopup()` opens Google OAuth
3. Frontend receives Firebase ID token
4. Backend verifies token with Firebase Admin SDK
5. Backend creates session (30-day expiry)
6. Session ID stored in localStorage
7. User redirected to dashboard

### Logout Process (FIXED ✅):
1. **Firebase `signOut(auth)`** - Clears OAuth state
2. **Backend `/api/auth/logout`** - Invalidates session
3. **Clear localStorage** - Removes session ID
4. **Clear sessionStorage** - Removes temp data
5. **Clear Firebase IndexedDB** - Prevents stale state
6. **Redirect to login** - Fresh start

---

## 📁 File Management

### Features:
- Upload files (.py, .js, .txt, etc.)
- Start/stop bots
- Real-time logs
- File editor (for text files)
- Folder organization
- Pip package installer

### File Limits:
- **Free:** 10 files, 20 MB each
- **Subscribed:** 50 files, 50 MB each
- **Admin:** Unlimited

### Security:
- Malware scanning on upload
- Blocked extensions: .exe, .bat, .apk, etc.
- Dangerous code pattern detection

---

## 👥 User Roles

### Free User
- Upload 10 files
- 20 MB file size limit
- Basic features

### Subscribed User
- Upload 50 files
- 50 MB file size limit
- Priority support

### Admin
- Unlimited files
- User management
- Ban/unban users
- System settings

### Owner
- Full system access
- Database management
- Set in `OWNER_UID` variable in app.py

---

## 🛠️ API Endpoints

### Authentication
- `POST /api/auth/login` - Login with Firebase token
- `GET /api/auth/me` - Get current user
- `POST /api/auth/logout` - Logout (invalidate session)

### Files
- `GET /api/files/list` - List user files
- `POST /api/files/upload` - Upload file
- `POST /api/files/create` - Create new file
- `DELETE /api/files/delete/<id>` - Delete file
- `POST /api/files/action/<id>` - Start/stop file
- `GET /api/files/logs/<id>` - Get file logs
- `POST /api/files/pip/<id>` - Install pip package

### Admin
- `GET /api/admin/users` - List all users
- `POST /api/admin/user/role` - Change user role
- `POST /api/admin/user/ban` - Ban/unban user
- `GET /api/admin/stats` - System statistics

---

## 🎨 Frontend Tech Stack

- **Vanilla JavaScript** (ES6+)
- **Firebase SDK** (Auth + Database)
- **CSS3** (Custom styling, no frameworks)
- **Module imports** (ES modules)

### Key Features:
- Responsive design (mobile + desktop)
- Dark theme
- Toast notifications
- Modal dialogs
- Real-time updates

---

## 🗄️ Database Structure (Firebase RTDB)

```
/users/{uid}
  - email, name, role, banned, created_at, last_login

/sessions/{session_id}
  - uid, created_at, expires_at, role, email, name

/files/{uid}/{file_id}
  - id, name, size, type, status, upload_time, folder_id

/file_content/{uid}/{file_id}
  - content_b64, updated_at

/file_logs/{uid}/{file_id}/{log_id}
  - ts, msg

/activity/{activity_id}
  - uid, action, icon, msg, time

/bot_commands/{uid}/{cmd_id}
  - command, payload, status, created_at

/notifications/{uid}/{notif_id}
  - text, type, time, read

/subusers/{owner_uid}/active/{sub_uid}
  - permissions, added_at

/folders/{uid}/{folder_id}
  - name, created_at
```

---

## 🔒 Security Features

### Authentication
- Firebase Admin SDK token verification
- Session expiry (30 days)
- Rate limiting (login, upload, pip)
- CSRF protection

### File Upload
- Extension whitelist/blacklist
- Magic byte detection
- Malware keyword scanning
- Size limits per role

### API Security
- Session validation on all routes
- Role-based access control (RBAC)
- Admin-only endpoints protected
- Input sanitization

---

## 🐛 Troubleshooting

### Issue: "Continue with Google" not working
**Solution:** Logout fix implemented! Firebase `signOut()` now properly called.

### Issue: Session expired
**Solution:** Re-login. Sessions expire after 30 days.

### Issue: File won't start
**Solution:** Check file logs. Only .py and .js files can run as bots.

### Issue: Pip install fails
**Solution:** Bot must be running. Start the bot first, then install packages.

---

## 📝 Development Notes

### Firebase Config
Update Firebase config in `templates/index.html`:
```javascript
const app = initializeApp({
  apiKey: "YOUR_API_KEY",
  authDomain: "YOUR_PROJECT.firebaseapp.com",
  projectId: "YOUR_PROJECT_ID",
  // ...
});
```

### Owner UID
Set your Firebase UID in `app.py`:
```python
OWNER_UID = "your-firebase-uid-here"
```

### Telegram Bot (Optional)
Telegram integration is deprecated but bot username can be set:
```python
BOT_NAME = "YourBotName"  # without @
```

---

## 📜 License
Proprietary - All rights reserved

---

## 👨‍💻 Support
For technical support, create a support ticket in the app.

---

## ✅ Changelog

### v9-v7-logout-fixed (March 2, 2025)
- ✅ **CRITICAL FIX:** Added Firebase `signOut()` to all logout functions
- ✅ Fixed "Continue with Google" authentication flow
- ✅ Added Firebase IndexedDB cleanup on logout
- ✅ Implemented proper 5-step logout process
- ✅ Updated all template files (9 files)
- ✅ Added comprehensive error handling
- ✅ Created detailed fix documentation

### v9-v7-auth-fixed
- Previous version with auth improvements

---

**🔥 This version includes the complete logout fix as requested!**
