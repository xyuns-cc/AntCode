#!/usr/bin/env node
/** Fail closed on unknown HIGH/CRITICAL npm audit findings. */

import fs from "node:fs";

const ADVISORY_ID = "GHSA-qwww-vcr4-c8h2";
const ADVISORY_URL = `https://github.com/advisories/${ADVISORY_ID}`;
const EXPIRES_ON = "2026-08-31";
const BLOCKING_SEVERITIES = new Set(["high", "critical"]);
const ROOT_PACKAGE = "react-router";
const DERIVED_PACKAGE = "react-router-dom";

function loadReport(path) {
  let payload;
  try {
    payload = JSON.parse(fs.readFileSync(path, "utf8"));
  } catch (error) {
    throw new Error(`cannot read npm audit JSON: ${error.message}`);
  }
  if (payload?.auditReportVersion !== 2 || !isRecord(payload.vulnerabilities)) {
    throw new Error("malformed npm audit report: expected auditReportVersion 2");
  }
  return payload;
}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isBlocking(vulnerability) {
  return BLOCKING_SEVERITIES.has(String(vulnerability.severity).toLowerCase());
}

function isExactAdvisory(via) {
  return (
    isRecord(via) &&
    via.url === ADVISORY_URL &&
    via.name === ROOT_PACKAGE &&
    String(via.severity).toLowerCase() === "high"
  );
}

function isAllowedRoot(name, vulnerability) {
  return (
    name === ROOT_PACKAGE &&
    vulnerability.name === ROOT_PACKAGE &&
    Array.isArray(vulnerability.via) &&
    vulnerability.via.length === 1 &&
    isExactAdvisory(vulnerability.via[0])
  );
}

function isAllowedDerived(name, vulnerability) {
  return (
    name === DERIVED_PACKAGE &&
    vulnerability.name === DERIVED_PACKAGE &&
    Array.isArray(vulnerability.via) &&
    vulnerability.via.length === 1 &&
    vulnerability.via[0] === ROOT_PACKAGE
  );
}

function validateDate(asOf) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(asOf)) {
    throw new Error(`invalid policy date: ${asOf}`);
  }
  if (asOf > EXPIRES_ON) {
    throw new Error(`${ADVISORY_ID} exception expired on ${EXPIRES_ON}`);
  }
}

function describe(name, vulnerability) {
  const severity = String(vulnerability.severity || "unknown").toUpperCase();
  const via = Array.isArray(vulnerability.via) ? JSON.stringify(vulnerability.via) : "invalid via";
  return `${name}: ${severity}, via=${via}`;
}

export function evaluateReport(report, asOf) {
  if (report?.auditReportVersion !== 2 || !isRecord(report.vulnerabilities)) {
    throw new Error("malformed npm audit report: expected auditReportVersion 2");
  }
  const blocking = Object.entries(report.vulnerabilities).filter(([, item]) => isBlocking(item));
  if (blocking.length === 0) {
    return [];
  }
  validateDate(asOf);
  return blocking
    .filter(([name, item]) => !isAllowedRoot(name, item) && !isAllowedDerived(name, item))
    .map(([name, item]) => describe(name, item));
}

function main() {
  const [reportPath, asOf = new Date().toISOString().slice(0, 10)] = process.argv.slice(2);
  if (!reportPath) {
    throw new Error("usage: check_npm_audit.mjs <npm-audit.json> [YYYY-MM-DD]");
  }
  const failures = evaluateReport(loadReport(reportPath), asOf);
  if (failures.length > 0) {
    console.error(`[fail] npm audit: ${failures.length} unapproved HIGH/CRITICAL finding(s)`);
    failures.forEach((failure) => console.error(`  - ${failure}`));
    return 1;
  }
  console.log(`[ok] npm audit: no unapproved HIGH/CRITICAL findings (${ADVISORY_ID} expires ${EXPIRES_ON})`);
  return 0;
}

try {
  process.exitCode = main();
} catch (error) {
  console.error(`[fail] npm audit gate: ${error.message}`);
  process.exitCode = 1;
}
