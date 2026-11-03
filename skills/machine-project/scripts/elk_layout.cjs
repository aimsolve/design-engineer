// The machine schemes' layout (scheme_draw.py): stdin = a JSON list of ELK graphs, stdout = the same graphs laid out.
// ELK = Eclipse Layout Kernel, vendor/elk.bundled.js (elkjs 0.9.3, EPL-2.0).
const ELK = require('./vendor/elk.bundled.js');
let s = '';
process.stdin.on('data', (d) => { s += d; }).on('end', () => {
  const elk = new ELK();
  Promise.all(JSON.parse(s).map((g) => elk.layout(g)))
    .then((gs) => process.stdout.write(JSON.stringify(gs)), (e) => { console.error(String(e && e.stack || e)); process.exit(1); });
});
