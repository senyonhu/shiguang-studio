"use strict";
// Repeat the same actual gameplay and protocol checks against the distinct formal-manifest packages.
const path = require("node:path");
process.env.HTML_MINIGAME_TEST_ROOT = path.join(__dirname, "../production");
require("./sdk.test.cjs");
require("./gameplay.test.cjs");
