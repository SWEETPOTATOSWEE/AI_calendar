# redirect_uri_mismatch Error Resolution Guide

## Problem Symptoms

The following error occurs when attempting Google login:
```
400 Error: redirect_uri_mismatch
```

## Cause

The redirect URI registered in Google Cloud Console does not match the redirect URI being requested.

In Codespaces environments, this problem frequently occurs because the **URL changes every time you create a new Codespace**.

## Solution

### Step 1: Delete GOOGLE_REDIRECT_URI from Codespaces Secrets

**Important**: Do NOT set `GOOGLE_REDIRECT_URI` in Codespaces Secrets!

1. Go to GitHub repository → **Settings**
2. **Secrets and variables** → **Codespaces**
3. Find `GOOGLE_REDIRECT_URI` and click **Delete**

This allows the `dev-run.sh` script to automatically detect the current Codespace URL.

### Step 2: Check Current Codespace's Redirect URI

```bash
/workspaces/AI_calendar/scripts/show-redirect-uri.sh
```

Example output:
```
https://your-codespace-name-3000.app.github.dev/auth/google/callback
```

### Step 3: Add URI to Google Cloud Console

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Select **APIs & Services** → **Credentials**
3. Click to edit your OAuth 2.0 Client ID
4. In the **Authorized redirect URIs** section:
   - Click **ADD URI**
   - **Exactly** copy and paste the URI from Step 2
   - Must match exactly - no spaces, no trailing slash
5. Click **SAVE**
6. **Wait about 5 minutes** (time for changes to propagate to Google servers)

### Step 4: Restart Application

```bash
# Stop existing processes
pkill -f "uvicorn"
pkill -f "next dev"

# Restart application
/workspaces/AI_calendar/scripts/dev-run.sh
```

Verify the console output shows:
```
=== Environment Variables ===
BACKEND_PUBLIC_BASE: https://your-codespace-name-8000.app.github.dev
FRONTEND_BASE_URL: https://your-codespace-name-3000.app.github.dev
GOOGLE_REDIRECT_URI: https://your-codespace-name-3000.app.github.dev/auth/google/callback
COOKIE_SECURE: 1
=================
```

### Step 5: Test

1. Access the application in your browser
2. Attempt Google login
3. If error still occurs:
   - Open browser developer tools (F12) → Network tab
   - Check the `/auth/google/login` request
   - Verify the `redirect_uri` parameter value in the Redirect URL
   - Confirm it **exactly** matches the URI registered in Google Cloud Console

## Common Mistakes

### ❌ Trailing Slash Difference
```
Registered: https://...3000.app.github.dev/auth/google/callback/
Actual:     https://...3000.app.github.dev/auth/google/callback
→ Mismatch!
```

### ❌ HTTP vs HTTPS
```
Registered: http://...3000.app.github.dev/auth/google/callback
Actual:     https://...3000.app.github.dev/auth/google/callback
→ Mismatch!
```

### ❌ Using Old Codespace URL
```
Fixed URL in Codespaces Secrets:
GOOGLE_REDIRECT_URI=https://old-codespace-3000.app.github.dev/auth/google/callback

New Codespace requires different URL!
→ Delete from Secrets and use auto-detection
```

### ❌ Port Number Difference
```
Registered: https://...3000.app.github.dev/auth/google/callback
Actual:     https://...8000.app.github.dev/auth/google/callback
→ Must use frontend port (3000), not backend port!
```

## Using Multiple Codespaces

If you use multiple Codespaces simultaneously, you can add all their URIs to Google Cloud Console:

```
https://codespace1-name-3000.app.github.dev/auth/google/callback
https://codespace2-name-3000.app.github.dev/auth/google/callback
https://codespace3-name-3000.app.github.dev/auth/google/callback
http://localhost:3000/auth/google/callback
```

You can add up to 100 URIs.

## If Problem Persists

Check backend logs to verify the actual redirect_uri being used:

```bash
# Check backend logs in terminal
# Look for "[DEBUG] Google OAuth redirect_uri:" line
```

Or:

```bash
# After clicking Google login in browser
# Copy the entire error page URL
# Add the redirect_uri= parameter value to Google Cloud Console
```
