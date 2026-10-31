// 独立加载，ETF 数据或图表失败不影响原宝妈指数。
(() => {
  const status = document.getElementById('etf-oi-status');
  const content = document.getElementById('etf-oi-content');
  const finite = value => Number.isFinite(value) ? value : null;
  const format = (value, digits = 2) => Number.isFinite(value)
    ? value.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
    : '—';
  const text = (id, value) => { document.getElementById(id).textContent = value; };
  const xAxis = () => ({
    offset: true,
    grid: { display: false },
    ticks: {
      color: '#94a3b8', maxTicksLimit: 8, maxRotation: 0,
      callback(value) { return this.getLabelForValue(value).slice(5); },
    },
  });
  const yAxis = (title, color, position = 'left') => ({
    position,
    afterFit: axis => { axis.width = 64; },
    title: { display: true, text: title, color },
    grid: { color: position === 'left' ? '#33415555' : 'transparent' },
    ticks: { color, maxTicksLimit: 6 },
  });

  fetch('data/csi300_etf_position.json', { cache: 'no-cache' })
    .then(response => {
      if (!response.ok) throw new Error('unavailable');
      return response.json();
    })
    .then(data => {
      if (data.methodology_id !== 'csi300_etf_free_float_v1') throw new Error('methodology');
      const all = (Array.isArray(data.records) ? data.records : [])
        .filter(row => row && typeof row.date === 'string')
        .sort((a, b) => a.date.localeCompare(b.date));
      const complete = all.filter(row => finite(row.ratio_pct) !== null);
      if (!complete.length) throw new Error('empty');
      const latest = complete[complete.length - 1];
      const records = all.slice(-250);
      const gaps = records.filter(row => finite(row.ratio_pct) === null).length;
      text('etf-oi-date', `最新完整数据：${latest.date} · 最近 ${records.length} 个交易日`);
      text('etf-oi-ratio', `${format(latest.ratio_pct, 4)}%`);
      text('etf-oi-assets', format(finite(latest.etf_assets_yuan) === null ? null : latest.etf_assets_yuan / 1e8));
      text('etf-oi-market-value', format(finite(latest.index_free_float_mv_yuan) === null ? null : latest.index_free_float_mv_yuan / 1e12));
      text('etf-oi-close', format(latest.index_close));
      text('etf-oi-funds', `当日样本：${format(latest.fund_count, 0)} 只普通沪深300 ETF；按实际上市/退市日期纳入。`);
      const notices = [];
      if (all[all.length - 1].date > latest.date) {
        notices.push(`最新交易日 ${all[all.length - 1].date} 数据尚未齐全，当前占比截至 ${latest.date}。`);
      }
      if (gaps) notices.push(`图示区间有 ${gaps} 个交易日数据未齐，缺口处不连线。`);
      if (!records.some(row => finite(row.volume_shares) !== null)) notices.push('成交量数据暂缺。');
      status.textContent = notices.join(' ');
      content.hidden = false;

      try {
        const labels = records.map(row => row.date);
        const shared = {
          responsive: true, maintainAspectRatio: false, animation: false,
          interaction: { mode: 'index', intersect: false },
        };
        new Chart(document.getElementById('chart-etf-oi'), {
          type: 'line',
          data: { labels, datasets: [
            {
              label: '配置强度代理', yAxisID: 'ratio',
              data: records.map(row => finite(row.ratio_pct)),
              borderColor: '#fbbf24', backgroundColor: '#fbbf24',
              borderWidth: 2, pointRadius: 1.5, pointHoverRadius: 4, spanGaps: false,
            },
            {
              label: '沪深300', yAxisID: 'price',
              data: records.map(row => finite(row.index_close)),
              borderColor: '#60a5fa', backgroundColor: '#60a5fa',
              borderWidth: 2, pointRadius: 1.5, pointHoverRadius: 4, spanGaps: false,
            },
          ] },
          options: {
            ...shared,
            plugins: {
              legend: { labels: { color: '#cbd5e1', boxWidth: 12 } },
              tooltip: { callbacks: { label: ctx => ctx.dataset.yAxisID === 'ratio'
                ? `配置强度代理：${format(ctx.parsed.y, 4)}%`
                : `沪深300：${format(ctx.parsed.y)} 点` } },
            },
            scales: {
              x: xAxis(), ratio: yAxis('占比 (%)', '#fbbf24'),
              price: yAxis('沪深300 (点)', '#60a5fa', 'right'),
            },
          },
        });
        new Chart(document.getElementById('chart-etf-volume'), {
          type: 'bar',
          data: { labels, datasets: [{
            label: '沪深300成交量',
            data: records.map(row => finite(row.volume_shares) === null ? null : row.volume_shares / 1e8),
            backgroundColor: records.map(row => {
              if (finite(row.index_close) === null || finite(row.index_pre_close) === null) return '#475569';
              return row.index_close > row.index_pre_close ? '#ef4444'
                : row.index_close < row.index_pre_close ? '#22c55e' : '#475569';
            }),
          }] },
          options: {
            ...shared,
            plugins: {
              legend: { display: false },
              tooltip: { callbacks: { label: ctx => `成交量：${format(ctx.parsed.y)} 亿股` } },
            },
            scales: {
              x: xAxis(), y: { ...yAxis('亿股', '#94a3b8'), beginAtZero: true },
              right: {
                position: 'right', afterFit: axis => { axis.width = 64; },
                grid: { display: false }, border: { display: false }, ticks: { display: false },
              },
            },
          },
        });
      } catch (error) {
        status.textContent = `${status.textContent} 图表暂时无法显示，以上为最新完整数据。`;
      }
    })
    .catch(() => {
      content.hidden = true;
      status.textContent = 'ETF 数据暂不可用，请稍后刷新。';
    });
})();
