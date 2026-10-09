import {readFile, writeFile} from 'node:fs/promises';

const endpoint = process.env.SHOPIFY_GRAPHQL_ENDPOINT;
if (!endpoint?.startsWith('http://[::1]:3457/graphiql/graphql.json?')) {
  throw new Error('SHOPIFY_GRAPHQL_ENDPOINT must be the local Shopify app dev GraphiQL proxy');
}

const [ordersFile, abandonedFile, ordersOutput, abandonedOutput] = process.argv.slice(2);
if (![ordersFile, abandonedFile, ordersOutput, abandonedOutput].every(Boolean)) {
  throw new Error('Expected two query files and two output files');
}

async function execute(queryFile, variables, outputFile) {
  const query = await readFile(queryFile, 'utf8');
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({query, variables}),
  });
  const payload = await response.json();
  if (!response.ok || payload.errors?.length) {
    throw new Error(JSON.stringify({status: response.status, errors: payload.errors}));
  }
  await writeFile(outputFile, JSON.stringify(payload), {mode: 0o600});
  return Object.values(payload.data ?? {})[0]?.nodes?.length ?? 0;
}

const results = await Promise.allSettled([
  execute(
    ordersFile,
    {first: 50, after: null, query: 'created_at:>=2026-09-25'},
    ordersOutput,
  ),
  execute(
    abandonedFile,
    {first: 50, after: null, query: 'created_at:>=2026-09-25 status:open'},
    abandonedOutput,
  ),
]);
const summary = {
  orders: results[0].status === 'fulfilled' ? results[0].value : null,
  abandonedCheckouts: results[1].status === 'fulfilled' ? results[1].value : null,
  errors: results.flatMap((result, index) => result.status === 'rejected'
    ? [{operation: index === 0 ? 'orders' : 'abandonedCheckouts', message: result.reason.message}]
    : []),
};
console.log(JSON.stringify(summary));
if (summary.errors.length) process.exitCode = 1;
