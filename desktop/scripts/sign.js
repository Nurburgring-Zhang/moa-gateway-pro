'use strict';

/**
 * scripts/sign.js — Windows Authenticode signing hook for electron-builder 26.x.
 *
 * Wired from electron-builder.yml via `win.signtoolOptions.sign`.
 * electron-builder invokes this function once per artifact and per hash with:
 *   configuration: { path, hash ('sha1'|'sha256'), isNest, options, ... }
 *   packager:      the electron-builder Packager instance
 *
 * The certificate is environment-driven:
 *
 *   set MOA_SIGN_PFX_FILE=C:\secrets\moagateway.pfx
 *   set MOA_SIGN_PFX_PASSWORD=***
 *
 * When those are absent (developer machine, CI without the signing secret)
 * the hook returns WITHOUT signing and says so loudly — the build completes
 * with an UNSIGNED artifact. That is deliberate: unsigned local builds must
 * always stay possible, while a release pipeline that provides the
 * certificate automatically gets signed binaries.
 *
 * Alternative: skip this hook's env vars and use electron-builder's stock
 * mechanism (WIN_CSC_LINK / WIN_CSC_KEY_PASSWORD or signtoolOptions.certificateFile)
 * — see README.md.
 */

const path = require('node:path');
const { execFile } = require('node:child_process');

const DEFAULT_TIMESTAMP_SERVER = 'http://timestamp.digicert.com';
const SIGN_TIMEOUT_MS = parseInt(process.env.SIGNTOOL_TIMEOUT, 10) || 10 * 60 * 1000;

/** Build the signtool.exe argument list for one artifact + hash. */
function buildArgs(configuration, pfxFile, pfxPassword) {
  const hash = configuration.hash === 'sha1' ? 'sha1' : 'sha256';
  const signtoolOptions =
    (configuration.options && configuration.options.signtoolOptions) || {};
  const args = ['sign', '/f', pfxFile, '/p', pfxPassword, '/fd', hash];

  if (process.env.ELECTRON_BUILDER_OFFLINE !== 'true') {
    // Nested (second) signatures and sha256 use RFC 3161 (/tr + /td);
    // a primary sha1 signature uses the legacy Authenticode timestamp (/t).
    const useRfc3161 = configuration.isNest || hash === 'sha256';
    const server =
      (useRfc3161
        ? signtoolOptions.rfc3161TimeStampServer
        : signtoolOptions.timeStampServer) || DEFAULT_TIMESTAMP_SERVER;
    if (useRfc3161) {
      args.push('/tr', server, '/td', hash);
    } else {
      args.push('/t', server);
    }
  }

  if (configuration.isNest) args.push('/as'); // append (dual-sign)
  args.push('/debug');
  args.push(configuration.path); // file must be last
  return args;
}

/** Run signtool with retries for the classic transient failures. */
function execWithRetry(tool, args, env) {
  const attempts = 3;
  const runOnce = () =>
    new Promise((resolve, reject) => {
      execFile(
        tool,
        args,
        { timeout: SIGN_TIMEOUT_MS, maxBuffer: 10 * 1024 * 1024, env, windowsHide: true },
        (err, stdout, stderr) => {
          if (err) {
            err.stderr = stderr;
            err.stdout = stdout;
            reject(err);
          } else {
            resolve(stdout);
          }
        },
      );
    });

  return (async () => {
    let lastErr;
    for (let i = 0; i < attempts; i += 1) {
      try {
        // eslint-disable-next-line no-await-in-loop
        return await runOnce();
      } catch (err) {
        lastErr = err;
        const msg = `${err.message || err}\n${err.stderr || ''}`;
        const transient =
          msg.includes('being used by another process') ||
          msg.includes('timestamp server') ||
          msg.includes('Couldn\'t resolve host name');
        if (!transient || i === attempts - 1) {
          throw new Error(`signtool failed: ${msg}`);
        }
        console.warn(`[sign] transient failure, retrying in 15s: ${msg.split('\n')[0]}`);
        // eslint-disable-next-line no-await-in-loop
        await new Promise((r) => setTimeout(r, 15000));
      }
    }
    throw lastErr;
  })();
}

module.exports = async function sign(configuration, packager) {
  const pfxFile = process.env.MOA_SIGN_PFX_FILE || '';
  const pfxPassword = process.env.MOA_SIGN_PFX_PASSWORD || '';
  const artifact = path.basename(configuration.path);

  if (!pfxFile) {
    console.warn(
      `[sign] SKIP ${artifact}: no code-signing certificate configured ` +
        '(set MOA_SIGN_PFX_FILE + MOA_SIGN_PFX_PASSWORD to enable Authenticode signing). ' +
        'The artifact will be UNSIGNED.',
    );
    return;
  }

  const args = buildArgs(configuration, pfxFile, pfxPassword);

  // Resolve signtool.exe through electron-builder's own toolset management
  // (downloads/locates the Windows SDK signtool consistently across machines).
  const manager = await packager.signingManager.value;
  const toolInfo = await manager.getToolPath(true);

  console.log(
    `[sign] signing ${artifact} (${configuration.hash}${configuration.isNest ? ', nested' : ''}) ` +
      `with ${path.basename(pfxFile)}`,
  );
  await execWithRetry(toolInfo.path, args, { ...process.env, ...(toolInfo.env || {}) });
  console.log(`[sign] signed ${artifact}`);
};
