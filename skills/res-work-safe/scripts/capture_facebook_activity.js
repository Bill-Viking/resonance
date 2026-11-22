(input) => {
  // Read only visible activity-log structure. The caller supplies observed boundaries.
  const visible = (element) => {
    if (!element || element.hidden || element.getAttribute('aria-hidden') === 'true') return false;
    if (typeof getComputedStyle === 'function') {
      const style = getComputedStyle(element);
      if (style.display === 'none' || style.visibility === 'hidden') return false;
    }
    return element.getClientRects ? element.getClientRects().length > 0 : true;
  };
  const words = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const safeURL = (href) => {
    try {
      if (typeof href !== 'string' || /[\s\\]/.test(href)) return null;
      const url = new URL(href);
      return url.protocol === 'https:' && !url.username && !url.password && !url.port && !url.hash &&
        ['facebook.com', 'www.facebook.com', 'm.facebook.com'].includes(url.hostname) ? url : null;
    } catch (_) { return null; }
  };
  const profile = (href) => {
    const url = safeURL(href);
    if (!url) return '';
    const path = url.pathname.replace(/\/$/, '');
    if (path === '/profile.php') {
      const ids = url.searchParams.getAll('id');
      return ids.length === 1 && /^\d+$/.test(ids[0]) ?
        'https://www.facebook.com/profile.php?id=' + ids[0] : '';
    }
    return /^\/[A-Za-z0-9_.]+$/.test(path) &&
      !['/me','/groups','/watch','/reel','/photo.php','/permalink.php','/profile.php','/allactivity'].includes(path) ?
      'https://www.facebook.com' + path : '';
  };
  const semantic = ['comment_id','fbid','id','reply_comment_id','story_fbid','v'];
  const navigation = (href) => {
    const url = safeURL(href);
    if (!url) return '';
    const path = url.pathname.replace(/\/$/, '') || '/';
    const values = new URLSearchParams();
    for (const key of semantic) {
      const found = url.searchParams.getAll(key);
      if (found.length > 1 || found.some((value) => !value.trim())) return '';
      if (found.length) values.set(key, found[0]);
    }
    const supported = /^\/[^/]+\/posts\/[^/]+$/.test(path) ||
      /^\/groups\/[^/]+\/(?:posts|permalink)\/[^/]+$/.test(path) ||
      /^\/(?:[^/]+\/)?(?:photos|videos)\/[^/]+(?:\/[^/]+)?$/.test(path) ||
      /^\/reel\/[^/]+$/.test(path) ||
      path === '/permalink.php' && values.has('story_fbid') && values.has('id') ||
      (path === '/photo.php' || path === '/photo') && values.has('fbid') ||
      path === '/watch' && values.has('v');
    const query = values.toString();
    return supported ? 'https://www.facebook.com' + path + (query ? '?' + query : '') : '';
  };
  const page = (href) => {
    const url = safeURL(href);
    return url && /^\/[^/]+\/allactivity\/?$/i.test(url.pathname) ?
      'https://www.facebook.com' + url.pathname.replace(/\/$/, '') : '';
  };
  const heading = Array.from(document.querySelectorAll('h2'))
    .some((node) => visible(node) && words(node.innerText || node.textContent) === 'Comments');
  const pageURL = page(location.href);
  const category = heading && pageURL ? 'comments' : 'unknown';
  const root = document.querySelector('main') || document.body;
  const views = Array.from(root.querySelectorAll('a[href]')).filter((node) =>
    visible(node) && /^View(?:\b|$)/i.test(words(node.getAttribute('aria-label') || node.innerText || node.textContent)));
  const rows = [];
  const parsedCheckboxes = new Set();
  const parsedViews = new Set();
  for (const view of views) {
    let container = view.parentElement;
    while (container && container !== root.parentElement) {
      const checkboxes = Array.from(container.querySelectorAll('input[type="checkbox"]')).filter(visible);
      const rowViews = Array.from(container.querySelectorAll('a[href]')).filter((node) =>
        visible(node) && /^View(?:\b|$)/i.test(words(node.getAttribute('aria-label') || node.innerText || node.textContent)));
      if (checkboxes.length === 1 && rowViews.length === 1 && rowViews[0] === view) {
        const checkbox = checkboxes[0];
        const rowKey = words(checkbox.getAttribute('aria-label'));
        if (/^Select post from .+, post \d+$/i.test(rowKey)) {
          const author = Array.from(container.querySelectorAll('a[href]'))
            .find((anchor) => anchor !== view);
          const label = words(view.getAttribute('aria-label') || view.innerText || view.textContent);
          const kind = /\brepl(?:y|ies|ied)\b/i.test(label) ? 'reply' :
            /\bcomment(?:s|ed)?\b/i.test(label) ? 'comment' : 'unknown';
          rows.push({row_key: rowKey, kind,
                     author_url: author ? profile(author.href) : '', navigation_url: navigation(view.href)});
          parsedCheckboxes.add(checkbox);
          parsedViews.add(view);
        }
        break;
      }
      container = container.parentElement;
    }
  }
  const activityCheckboxes = Array.from(root.querySelectorAll('input[type="checkbox"]'))
    .filter((node) => {
      if (!visible(node)) return false;
      const labels = Array.from(node.labels || []).map((label) => words(label.innerText || label.textContent));
      return words(node.getAttribute('aria-label')) !== 'All' &&
        !(labels.length === 1 && labels[0] === 'All');
    });
  const unparsedRows = Math.max(
    activityCheckboxes.filter((node) => !parsedCheckboxes.has(node)).length,
    views.filter((node) => !parsedViews.has(node)).length,
    Array.from(root.querySelectorAll('[role="checkbox"]')).filter((node) => visible(node) && node.tagName !== 'INPUT').length,
    Array.from(root.querySelectorAll('button,[role="button"],[role="link"]')).filter((node) =>
      visible(node) && !parsedViews.has(node) &&
      /^View(?:\b|$)/i.test(words(node.getAttribute('aria-label') || node.innerText || node.textContent))).length,
    rows.length === 0 ? 1 : 0);
  const people = Array.from(root.querySelectorAll('button,[role="button"]')).filter((node) =>
    visible(node) && /^People\b/i.test(words(node.innerText || node.textContent)));
  const peopleFilter = people.length === 1 && words(people[0].innerText || people[0].textContent) === 'People All' ? 'all' : 'restricted';
  const search = Array.from(root.querySelectorAll('input[type="search"]')).filter((node) =>
    visible(node) && (node.getAttribute('aria-label') === 'Search activity log' ||
                      node.getAttribute('placeholder') === 'Search activity log'));
  const searchFilter = search.length === 1 && !String(search[0].value || '').trim() ? 'none' : 'restricted';
  const loading = Array.from(root.querySelectorAll('[role="status"][aria-label="Loading..."]')).some(visible);
  return {version: 1, account_url: profile(input.account_url), scope_id: input.scope_id,
          scope: input.scope, batch: {id: input.id, observed_at: input.observed_at,
          page_url: pageURL, category, account_verified: input.account_verified,
          people_filter: peopleFilter, search_filter: searchFilter,
          boundary: input.boundary, boundary_note: input.boundary_note,
          loading, unparsed_rows: unparsedRows, rows}};
}
