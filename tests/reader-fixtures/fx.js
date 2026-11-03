// Fixture-only behavior so the synthetic YouTube pages react to the two
// read-only controls the reader may use. Not part of the extension.
document.addEventListener('click', event => {
  const target = event.target;
  if (!target || !target.id) return;
  if (target.id === 'avatar-btn') {
    const menu = document.querySelector('#account-menu');
    if (!menu) return;
    if (menu.hasAttribute('hidden')) menu.removeAttribute('hidden');
    else menu.setAttribute('hidden', '');
  } else if (target.id === 'more') {
    const body = target.parentNode.querySelector('#content-text');
    if (body && body.getAttribute('data-full') !== null) body.textContent = body.getAttribute('data-full');
    target.setAttribute('hidden', '');
  }
});
