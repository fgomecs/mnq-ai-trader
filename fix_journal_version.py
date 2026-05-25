"""
Run this script from C:\\trading\\mnq-ai-trader to fix journal.html version display.
Usage: py -3.11 fix_journal_version.py
"""

import re

path = 'journal.html'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

original = content

# Fix 1: EMPTY constant hardcoded version
content = content.replace('version:"4.1.0"', 'version:"4.3.0"')

# Fix 2: Replace the entire load section with version polling
old_load = """fetch('journal_data.json?t=' + Date.now())
  .then(r => { if (!r.ok) throw new Error(); return r.json(); })
  .then(data => {
    render(data);
    // Pull live bot version from the running bot's dashboard file; patches
    // the badge + sidebar meta without re-rendering the whole journal.
    fetch('dashboard_data.json?t=' + Date.now())
      .then(r => r.json())
      .then(d => {
        if (d.botVersion) {
          window._botVersion = d.botVersion;
          const badge = document.getElementById('version-badge');
          if (badge) badge.textContent = 'V' + d.botVersion;
          const meta = document.getElementById('sb-meta');
          if (meta) {
            const lines = meta.innerHTML.split('<br>');
            lines[0] = 'V' + d.botVersion;
            meta.innerHTML = lines.join('<br>');
          }
        }
      })
      .catch(() => {});
  })
  .catch(() => {
    render(EMPTY);
    $('last-updated').textContent = 'No data yet — waiting for first session (Tue May 27, 2026)';
  });"""

new_load = """function applyVersion(ver) {
  if (!ver) return;
  window._botVersion = ver;
  const badge = document.getElementById('version-badge');
  if (badge) badge.textContent = 'V' + ver;
  const meta = document.getElementById('sb-meta');
  if (meta) {
    const lines = meta.innerHTML.split('<br>');
    lines[0] = 'V' + ver;
    meta.innerHTML = lines.join('<br>');
  }
}

function pollDashboardVersion() {
  fetch('dashboard_data.json?t=' + Date.now())
    .then(r => r.json())
    .then(d => { if (d.botVersion) applyVersion(d.botVersion); })
    .catch(() => {});
}
setInterval(pollDashboardVersion, 5000);

fetch('journal_data.json?t=' + Date.now())
  .then(r => { if (!r.ok) throw new Error(); return r.json(); })
  .then(data => {
    render(data);
    pollDashboardVersion();
  })
  .catch(() => {
    render(EMPTY);
    $('last-updated').textContent = 'No data yet — waiting for first session (Tue May 27, 2026)';
    pollDashboardVersion();
  });"""

content = content.replace(old_load, new_load)

if content == original:
    print("WARNING: No changes made — pattern not found exactly.")
    print("Applying fallback fix only...")
    # Fallback: just fix the EMPTY version which we know works
    # and add a simple version poll at the end of the script section
    content = original.replace('version:"4.1.0"', 'version:"4.3.0"')
    
    # Find the closing </script> and insert poll before it
    poll_code = """
// Live version poll from dashboard_data.json
setInterval(function() {
  fetch('dashboard_data.json?t=' + Date.now())
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (!d.botVersion) return;
      var badge = document.getElementById('version-badge');
      if (badge) badge.textContent = 'V' + d.botVersion;
      var meta = document.getElementById('sb-meta');
      if (meta) {
        var lines = meta.innerHTML.split('<br>');
        lines[0] = 'V' + d.botVersion;
        meta.innerHTML = lines.join('<br>');
      }
    }).catch(function(){});
}, 3000);
"""
    content = content.replace('</script>\n</body>', poll_code + '</script>\n</body>')
    print("Fallback fix applied.")
else:
    print("Full fix applied successfully.")

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("journal.html updated. Hard refresh browser (Ctrl+Shift+R).")
