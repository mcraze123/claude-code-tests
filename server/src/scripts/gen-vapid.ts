import webpush from 'web-push';

/**
 * Generates the VAPID keypair web push needs. Run once, paste into .env:
 *   npm run gen:vapid --workspace=server
 */
const keys = webpush.generateVAPIDKeys();
console.log(`VAPID_PUBLIC_KEY=${keys.publicKey}`);
console.log(`VAPID_PRIVATE_KEY=${keys.privateKey}`);
