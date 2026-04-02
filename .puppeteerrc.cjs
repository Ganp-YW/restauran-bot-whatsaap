const {join} = require('path');

/**
 * @type {import("puppeteer").Configuration}
 */
module.exports = {
  // Cambiamos el directorio donde se guarda Chromium para que Render no lo borre
  cacheDirectory: join(__dirname, '.cache', 'puppeteer'),
};
