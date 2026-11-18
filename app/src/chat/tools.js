'use strict';
// The app's tools for the assistant: one list, with every argument checked here in the main process before the
// call is passed to the window, which answers from the same in-memory data the screens are drawn from.
const ID = { type: 'string', pattern: '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$' };
const CUR = { type: 'string', pattern: '^[A-Z]{3}$', description: 'Currency code, e.g. USD, AUD, JPY' };
const DATE = { type: 'string', pattern: '^\\d{4}-\\d{2}-\\d{2}$', description: 'YYYY-MM-DD' };
const TENOR = { type: 'number', minimum: 0, maximum: 100, description: 'Term in years' };
const METRIC = { type: 'string', pattern: '^[a-z][a-z0-9_]{0,39}$', description: 'Metric key, e.g. crediting_rate (default), net_yield, mva_rate' };
const TYPE = { ...ID, description: 'Product type key, e.g. fixed_annuity, survival_weighted_annuity, whole_life_periodic_payout' };

const LANG = { type: 'string', enum: ['ja', 'en'], description: 'ja (default) reads the original; en reads the app\'s English translation of each page, where one exists' };

const TOOLS = [
  { name: 'list_products', description: 'All tracked products with id, English and Japanese names, insurer, product type(s), currencies, sale status and plan keys. Optional filters.',
    properties: { product_type: TYPE, insurer: { ...ID, description: 'Insurer id, e.g. dfl, msp, sml, tdf, nwl' } }, required: [] },
  { name: 'get_product', description: 'One product: facts, notes (which say what is unverified or assumed), plans with English labels and product types, metrics, distributor count, documents.',
    properties: { product_id: ID }, required: ['product_id'] },
  { name: 'get_rates', description: 'Published rate points for one product. Without date/from/to it returns the latest point of every matching series, with the previous point. With date: the point in force on that date. With from/to: every point in the range. Each point has its effective date, value in % per year (null = not offered), source URL, retrieval date and whether it came from the Wayback Machine.',
    properties: { product_id: ID, metric: METRIC, plan: { ...ID, description: 'Plan key' }, currency: CUR, term_years: TENOR,
      age: { type: 'number', minimum: 0, maximum: 120, description: 'Picks the age band containing this age' }, sex: { type: 'string', enum: ['F', 'M'] },
      date: DATE, from: DATE, to: DATE }, required: ['product_id'] },
  { name: 'leaderboard', description: 'Exactly what the Leaderboard screen shows for a product type, currency and tenor: every product-plan ranked by rate, with change, as-of date and stale flags. Uses the sample case (age, sex) set in the app where rates vary by age or sex.',
    properties: { product_type: TYPE, currency: CUR, tenor: TENOR, metric: METRIC }, required: ['product_type', 'currency', 'tenor'] },
  { name: 'moves', description: 'What changed between each series\' last two points: this period\'s changes, newly offered or suspended cells, and (separately) comparisons against a point more than 20 days older. Biggest first.',
    properties: { currency: CUR, insurer: ID, metric: METRIC }, required: [] },
  { name: 'current_view', description: 'What the app window shows now: screen, selections, open product, open document and page.', properties: {}, required: [] },
  { name: 'list_documents', description: 'Documents of a product: name, English label, Japanese title, local file path, retrieval or capture date, and flags: wayback copy, incomplete (truncated) capture, not downloaded. Read them with read_document and search_document.',
    properties: { product_id: ID }, required: ['product_id'] },
  { name: 'read_document', description: 'The text of pages of a product document (PDF), extracted by the app. Give a page or a range of at most 15 pages, e.g. "13" or "10-20". Returns each page number with its text (Japanese as printed). Figures and scanned pages have no text.',
    properties: { product_id: ID, name: { ...ID, description: 'Document name from list_documents' }, pages: { type: 'string', pattern: '^\\d{1,4}(-\\d{1,4})?$', description: 'Page or range, e.g. 13 or 10-20' }, language: LANG }, required: ['product_id', 'name', 'pages'] },
  { name: 'search_document', description: 'Find a word or phrase (Japanese or English, exact text) in a product document. Returns the page numbers with a snippet around each match, so you know which pages to read. Try the Japanese term first.',
    properties: { product_id: ID, name: { ...ID, description: 'Document name from list_documents' }, query: { type: 'string', description: 'Text to find, up to 60 characters' }, language: LANG }, required: ['product_id', 'name', 'query'] },
  { name: 'document_translation', description: 'Whether a product document has an English version in the app: which pages are translated, each page\'s status (ok, review, failed) and whether a translation is running. English versions are machine-made working translations.',
    properties: { product_id: ID, name: { ...ID, description: 'Document name from list_documents' } }, required: ['product_id', 'name'] },
  { name: 'news', description: 'News items the app tracks: insurers\' own releases and notices, regulator (FSA) and industry-association announcements, parent-group releases and press coverage. Each item has its date, English and Japanese title, topic, a short summary where one exists, the source kind and publisher, the URL, the insurers and tracked products it concerns, and flags (snippet_only = known from a search snippet only; checked = re-checked against the source). By default routine notices (maintenance, awards, campaigns) are left out. Newest first, at most `limit` (default 30, maximum 100).',
    properties: { insurer: { ...ID, description: 'Insurer id (see list_insurers), or a source owner such as fsa or liaj' }, product_id: ID,
      topic: { type: 'string', enum: ['product_launch', 'product_change', 'sales_suspension', 'rates', 'regulatory_conduct', 'regulation', 'distribution', 'results', 'corporate', 'customer_notice', 'other'] },
      source: { type: 'string', enum: ['official', 'parent', 'regulator', 'association', 'press'] },
      story: { ...ID, description: 'Story id from list_stories' }, from: DATE, to: DATE,
      query: { type: 'string', description: 'Words to find in titles and summaries (English or Japanese), up to 60 characters' },
      include_routine: { type: 'boolean' }, limit: { type: 'number', minimum: 1, maximum: 100 } }, required: [] },
  { name: 'list_stories', description: 'The stories the app follows (for example a regulatory case): id, title, status, insurers, start date, overview, key dates with their sources, open questions, and the number of linked items. Read the items with news and the story id.',
    properties: {}, required: [] },
  { name: 'list_insurers', description: 'All 41 life insurers on the association member list with id, names, news coverage tier (full = every release from their own lists; watch = product, suspension and regulatory items; market = only when the regulator, association or press names them), group, and for each news list when it was last read and whether it needs attention.',
    properties: {}, required: [] },
  { name: 'open_news', description: 'Navigate the app window to the News screen, optionally to one story or filtered to one insurer.',
    properties: { story: { ...ID, description: 'Story id' }, insurer: ID }, required: [] },
  { name: 'open_product', description: 'Navigate the app window to a product page.', properties: { product_id: ID }, required: ['product_id'] },
  { name: 'open_leaderboard', description: 'Navigate the app window to the Leaderboard for a product type, currency and tenor.',
    properties: { product_type: TYPE, currency: CUR, tenor: TENOR }, required: ['product_type', 'currency', 'tenor'] },
  { name: 'open_document', description: 'Show a document in the app\'s embedded viewer: switches to the product\'s Documents tab, selects the document by its name from list_documents, and jumps to the page.',
    properties: { product_id: ID, name: { ...ID, description: 'Document name from list_documents' }, page: { type: 'number', minimum: 1, maximum: 5000 } }, required: ['product_id', 'name'] },
];

function listForMcp() {
  return TOOLS.map((t) => ({ name: t.name, description: t.description,
    inputSchema: { type: 'object', properties: t.properties, required: t.required, additionalProperties: false } }));
}

// Returns { ok, args } with only known, well-formed arguments, or { ok: false, error }.
function validate(name, args) {
  const tool = TOOLS.find((t) => t.name === name);
  if (!tool) return { ok: false, error: `Unknown tool ${String(name).slice(0, 60)}.` };
  const input = args && typeof args === 'object' && !Array.isArray(args) ? args : {};
  const out = {};
  for (const key of Object.keys(input)) {
    if (!Object.prototype.hasOwnProperty.call(tool.properties, key)) return { ok: false, error: `Unknown argument "${String(key).slice(0, 40)}" for ${name}.` };
  }
  for (const [key, spec] of Object.entries(tool.properties)) {
    let v = input[key];
    if (v === undefined || v === null || v === '') {
      if (tool.required.includes(key)) return { ok: false, error: `${name} needs "${key}".` };
      continue;
    }
    if (spec.type === 'number') {
      if (typeof v === 'string' && /^\d{1,4}(\.\d+)?$/.test(v.replace(/[Yy]$/, ''))) v = Number(v.replace(/[Yy]$/, ''));
      if (typeof v !== 'number' || !Number.isFinite(v) || v < spec.minimum || v > spec.maximum) return { ok: false, error: `"${key}" must be a number from ${spec.minimum} to ${spec.maximum}.` };
    } else if (spec.type === 'boolean') {
      if (typeof v !== 'boolean') return { ok: false, error: `"${key}" must be true or false.` };
    } else {
      if (typeof v !== 'string' || v.length > 80) return { ok: false, error: `"${key}" must be a short string.` };
      if (spec.enum && !spec.enum.includes(v)) return { ok: false, error: `"${key}" must be one of ${spec.enum.join(', ')}.` };
      if (spec.pattern && !new RegExp(spec.pattern).test(v)) return { ok: false, error: `"${key}" is not in the expected form${spec.description ? ' (' + spec.description + ')' : ''}.` };
    }
    out[key] = v;
  }
  return { ok: true, args: out };
}

module.exports = { TOOLS, listForMcp, validate };
