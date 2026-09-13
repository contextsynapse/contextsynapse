const { createProxyMiddleware } = require('http-proxy-middleware');

module.exports = function(app) {
  const backendTarget = `http://127.0.0.1:${process.env.REACT_APP_BACKEND_PORT || '8000'}`;

  const onError = (err, req, res) => {
    if (res && !res.headersSent) {
      res.writeHead(503, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ detail: 'Backend starting up, please retry' }));
    }
  };

  const opts = { target: backendTarget, changeOrigin: true, onError };

  // Platform API routes
  app.use(
    ['/dashboard', '/auth', '/events', '/graphs', '/graph', '/admin', '/health', '/v1', '/api',
     '/cognition', '/shield', '/agent', '/intelligence', '/fusion', '/predictions', '/ready',
     '/aiql', '/query', '/context', '/search', '/billing', '/mcp', '/a2a', '/plugins',
     '/api/v1/pipeline', '/api/v1/stream', '/api/v1/webhook'],
    createProxyMiddleware(opts)
  );

  // PMS API routes — each explicitly listed to avoid catching page routes like /pms/portfolio
  app.use('/pms/portfolios',       createProxyMiddleware(opts));
  app.use('/pms/trades',           createProxyMiddleware(opts));
  app.use('/pms/clients',          createProxyMiddleware(opts));
  app.use('/pms/compliance',       createProxyMiddleware(opts));
  app.use('/pms/skills',           createProxyMiddleware(opts));
  // /pms/cockpit — NOT proxied here (conflicts with page route /pms/cockpit)
  // API calls use /pms/cockpit/:id which is handled via custom middleware below
  app.use('/pms/tax',              createProxyMiddleware(opts));
  app.use('/pms/fees',             createProxyMiddleware(opts));
  app.use('/pms/stocks',           createProxyMiddleware(opts));
  app.use('/pms/sectors',          createProxyMiddleware(opts));
  app.use('/pms/setup',            createProxyMiddleware(opts));
  app.use('/pms/auth',             createProxyMiddleware(opts));
  app.use('/pms/universe',         createProxyMiddleware(opts));
  app.use('/pms/catalog',          createProxyMiddleware(opts));
  app.use('/pms/sensors',          createProxyMiddleware(opts));
  app.use('/pms/risk',             createProxyMiddleware(opts));
  app.use('/pms/cash',             createProxyMiddleware(opts));
  app.use('/pms/model-portfolios', createProxyMiddleware(opts));
  app.use('/pms/ingest',           createProxyMiddleware(opts));
  app.use('/pms/credentials',      createProxyMiddleware(opts));
  app.use('/pms/simulate',         createProxyMiddleware(opts));
  app.use('/pms/feedback',           createProxyMiddleware(opts));
  app.use('/pms/stock-sensors',      createProxyMiddleware(opts));
  app.use('/pms/sensor-templates',   createProxyMiddleware(opts));
  app.use('/pms/corporate-actions',  createProxyMiddleware(opts));
  app.use('/pms/settlements',        createProxyMiddleware(opts));
  app.use('/pms/invoices',           createProxyMiddleware(opts));
  app.use('/pms/notifications',      createProxyMiddleware(opts));
  app.use('/pms/reports',            createProxyMiddleware(opts));
  app.use('/pms/nav',                createProxyMiddleware(opts));
  app.use('/pms/monitor',             createProxyMiddleware(opts));
  app.use('/pms/intelligence',       createProxyMiddleware(opts));
  app.use('/pms/earnings',           createProxyMiddleware(opts));
  app.use('/pms/mf-holdings',        createProxyMiddleware(opts));
  app.use('/pms/fii-dii',            createProxyMiddleware(opts));
  app.use('/pms/fund-managers',      createProxyMiddleware(opts));
  app.use('/pms/datasources',        createProxyMiddleware(opts));
  app.use('/pms/entities',           createProxyMiddleware(opts));

  // MF API routes
  app.use('/mf/schemes',        createProxyMiddleware(opts));
  app.use('/mf/nav',            createProxyMiddleware(opts));
  app.use('/mf/folios',         createProxyMiddleware(opts));
  app.use('/mf/transactions',   createProxyMiddleware(opts));
  app.use('/mf/sip',            createProxyMiddleware(opts));
  app.use('/mf/orders',         createProxyMiddleware(opts));
  app.use('/mf/expenses',       createProxyMiddleware(opts));
  app.use('/mf/distributors',   createProxyMiddleware(opts));
  app.use('/mf/compliance',     createProxyMiddleware(opts));
  app.use('/mf/auth',           createProxyMiddleware(opts));

  // /pms/cockpit/:id — proxy only when there's a portfolio ID after /cockpit/
  app.use('/pms/cockpit', (req, res, next) => {
    if (req.path && req.path.length > 1) {
      return createProxyMiddleware(opts)(req, res, next);
    }
    next();
  });

  // WebSocket
  app.use('/ws', createProxyMiddleware({ target: backendTarget, ws: true, changeOrigin: true }));
};
