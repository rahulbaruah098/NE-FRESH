# NE FRESH Profile, Email OTP and Splash Screen Update

## Updated safely

This update only touches the requested account/profile, customer email-change and splash/footer responsiveness areas.

### Admin profile
- Added `/admin/profile`.
- Added `templates/admin_profile.html`.
- Admin can update display name and phone.
- Admin can change login email after entering current password.
- Admin can change password after entering current password.
- Added Admin Profile & Password menu link in Admin panel.
- Updated Admin top-right profile dropdown links.

### Customer email change with OTP
- Customer profile now has a secure “Change Email with OTP” section.
- Customer enters a new email.
- OTP is sent to the new email.
- Email changes only after OTP verification.
- Existing email remains unchanged until verification succeeds.
- Duplicate email checks are included.
- OTP expires after 10 minutes and blocks after too many wrong attempts.

### Splash screen
- Splash displays only on first homepage load per browser tab/session.
- Splash does not show again on refresh.
- Website scrolling is locked while splash is visible.
- Splash uses a generated poster/thumbnail image for smoother loading.
- Video uses `object-fit: contain` so full content is visible across desktop, MacBook, iOS and mobile screens.
- Footer safe-area/mobile responsiveness was hardened.

## Not changed
- Order placement logic.
- Checkout calculation.
- Delivery routing logic.
- Platform fee logic.
- Store panel logic.
- Shiprocket/external delivery logic.
- Existing customer address and profile update logic, except adding email OTP change.

## .DS_Store note
`.DS_Store` is a macOS Finder metadata file. It may store folder view preferences such as icon layout, Finder sorting and folder display settings. It is not required for the Flask project and should not be committed to GitHub. Keep `.DS_Store` in `.gitignore` and remove existing tracked copies from the repository.
