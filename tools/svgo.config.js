module.exports = {
  plugins: [
    {
      name: 'preset-default',
      params: {
        overrides: {
          // Reduce coordinate precision aggressively (1 decimal = 0.1mm accuracy)
          convertPathData: { floatPrecision: 1 },
          convertTransform: { floatPrecision: 1 },
          // Keep class hooks used by runtime JS/CSS
          inlineStyles: false,
          // Keep data-* attributes on our LED circles
          removeUnknownsAndDefaults: {
            keepDataAttrs: true,
            keepAriaAttrs: true,
          },
          // Keep the led-markers id; strip everything else
          cleanupIds: {
            preserve: ['led-markers'],
          },
        },
      },
    },
    // Strip XML declaration and doctype
    'removeDoctype',
    'removeXMLProcInst',
  ],
};
