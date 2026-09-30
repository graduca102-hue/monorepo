/** Temporary probe: verifies Vercel picked up `/api` from the deployed root (`/api/health`). */
export default function health(req, res) {
  res.setHeader('Content-Type', 'application/json');
  return res.status(200).json({ ok: true, source: 'vercel-api' });
}
