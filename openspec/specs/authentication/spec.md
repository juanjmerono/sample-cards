# Authentication

## Overview
The application uses Oracle APEX built-in authentication with workspace user accounts.

## Authentication Scheme
- **Type**: `oracleApexAccounts` (Application Express Accounts)
- **Name**: Application Express Accounts
- **Public User**: `APEX_PUBLIC_USER`

## Authorization
- **Scheme**: `mustNotBePublicUser`
- All content pages require this authorization
- Pages with this scheme: 1–23

### Administration Rights
- **Name**: Administration Rights
- **Type**: PL/SQL function body
- **Logic**: `return true;` (always grants access)
- **Error**: "Insufficient privileges, user is not an Administrator"
- **Page Group**: Administration

## Security Settings
- **Deep Linking**: enabled
- **Embed in Frames**: `allowSameOrigin` only
- **Session State Protection**: URLs created after 1999-08-04 require checksum
- **Checksum Salt**: configured (hex value)
- **Session Management**: rejoin sessions as `publicSessions`
- **Form Auto Complete**: disabled on content pages
- **Page Access Protection**: `argumentsMustHaveChecksum`

## Login Page
- **Page**: 99999
- **Alias**: LOGIN
- **LOV**: `login-remember-username` (static, "Remember username" option)
