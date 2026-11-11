"""Fixed generated browser helpers; assets never name arbitrary local files."""

FILE_ACTION_HELPER = r"""
  const uploadFile = async (locator, args, timeout) => {
    const fs = await import('node:fs');
    const crypto = await import('node:crypto');
    const path = process.env.TAP_BROWSER_INPUTS_PATH || '/opt/browser/frozen/inputs.json';
    if (fs.statSync(path).size > 1000000) throw new Error('Frozen input exceeds limit');
    const manifest = JSON.parse(fs.readFileSync(path, 'utf8')).__lca_assets;
    const asset = manifest?.[args.asset_id];
    if (!asset || asset.sha256 !== args.sha256 || typeof asset.content_base64 !== 'string' || asset.content_base64.length > 341336) throw new Error('Upload asset unavailable');
    const bytes = Buffer.from(asset.content_base64, 'base64');
    if (!bytes.length || bytes.length > 256000 || crypto.createHash('sha256').update(bytes).digest('hex') !== args.sha256) throw new Error('Upload asset integrity failed');
    await locator.setInputFiles({name: asset.filename, mimeType: asset.content_type, buffer: bytes}, {timeout});
  };
  const captureDownload = async (page, locator, stepId, timeout) => {
    let download, stream, timer, listener;
    let active = true;
    const check = () => { if (!active) throw new Error('Download capture is no longer active'); };
    const work = async () => {
      const event = new Promise(resolve => {
        listener = item => { if (!active || download) { void item.cancel().catch(() => {}); return; } download = item; resolve(item); };
        page.on('download', listener);
      });
      await locator.click({timeout});
      await event;
      check();
      stream = await download.createReadStream();
      if (!active) { stream?.destroy(); check(); }
      if (!stream) throw new Error('Download stream unavailable');
      let size = 0;
      const chunks = [];
      for await (const chunk of stream) {
        check();
        size += chunk.length;
        if (size > 5000000) throw new Error('Download exceeds 5000000 bytes');
        chunks.push(chunk);
      }
      check();
      if (!size) throw new Error('Downloaded file is empty');
      const name = download.suggestedFilename().replace(/\\/g, '/').split('/').pop().replace(/[\x00-\x1f\x7f]/g, '').replace(/[^A-Za-z0-9._-]/g, '_').replace(/^\.+|\.+$/g, '').slice(0, 160) || 'download.bin';
      await test.info().attach(`lca-download:${stepId}:${name}`, {body: Buffer.concat(chunks, size), contentType: 'application/octet-stream'});
    };
    try {
      await Promise.race([work(), new Promise((_, reject) => { timer = setTimeout(() => { active = false; reject(new Error('Download capture timed out')); }, timeout); })]);
    } finally {
      active = false;
      clearTimeout(timer);
      if (listener) page.off('download', listener);
      if (stream) stream.destroy();
      if (download) { await download.cancel().catch(() => {}); await download.delete().catch(() => {}); }
    }
  };
"""
