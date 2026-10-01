import { defineConfig } from '@playwright/test';
export default defineConfig({testDir:'./tests/e2e',outputDir:'test-results/artifacts',timeout:120000,expect:{timeout:15000},workers:1,use:{baseURL:'http://127.0.0.1:5173',viewport:{width:1366,height:768},launchOptions:{channel:'msedge'},screenshot:'only-on-failure'},reporter:[['list'],['html',{open:'never',outputFolder:'test-results/report'}]]});
