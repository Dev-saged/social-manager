---
name: firebase-rules
description: Use when adding Firebase Auth, Firestore or Cloudflare backends. Enforces per-client projects, role-based Security Rules and public-config safety.
---

# Principles
- One Firebase project per client. The web config in CONFIG is public by design; security lives in Security Rules only.
- Roles live in users/{uid}.role and are written only from the console or an admin script, never from the client.
- Default deny. Allow per collection explicitly. Validate shape and size on write.
- Free tiers first. Cloudflare Pages for hosting, Workers only when a secret is needed (secrets never in client code).
- Add Firebase origins to the CSP meta only when actually used.

# Firestore rules template
```
rules_version = '2';
service cloud.firestore {
  match /databases/{db}/documents {
    function signedIn() { return request.auth != null; }
    function role() {
      return get(/databases/$(db)/documents/users/$(request.auth.uid)).data.role;
    }
    function staff() { return signedIn() && role() in ['admin', 'staff']; }
    function admin() { return signedIn() && role() == 'admin'; }

    match /users/{uid} {
      allow read: if signedIn() && request.auth.uid == uid;
      allow write: if false;
    }

    // repeat per collection
    match /items/{id} {
      allow read: if signedIn();
      allow create, update: if staff()
        && request.resource.data.title is string
        && request.resource.data.title.size() <= 200;
      allow delete: if admin();
    }
  }
}
```

# Checklist before shipping
- Rules tested in the Firebase emulator or Rules Playground for each role.
- No wildcard `/{document=**}` allow rules.
- Authentication providers limited to what the client uses.
- License expiry also enforced in rules or a Worker, not only in client code.
