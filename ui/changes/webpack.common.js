const path = require('path');
const CopyPlugin = require("copy-webpack-plugin");
const { CleanWebpackPlugin } = require('clean-webpack-plugin');
const ESLintPlugin = require('eslint-webpack-plugin');

module.exports = {
  mode: 'development',
  devtool: 'inline-source-map',
  entry: {
    index: './src/index.js',
    testing: './src/testing.js',
    graph: './src/graph.js',
  },
  output: {
    path: path.resolve(__dirname, 'dist'),
  },
  plugins: [
    // TODO: Fix eslint errors.
    // new ESLintPlugin(),
    new CleanWebpackPlugin(),
    new CopyPlugin({
      patterns: [
        { context: "src", from: "*.html" },
        { from: "src/css", to: "css" },
      ],
    }),
  ],
};
