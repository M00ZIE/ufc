/**
 * Auth helpers (login / register pages).
 * Mesma origem: cookies de sessão com SameSite=Lax.
 */
async function apiPostJson(path, body) {
  const res = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body || {}),
  });
  let data;
  try {
    data = await res.json();
  } catch (_) {
    throw new Error("Resposta inválida do servidor.");
  }
  return { res, data };
}
