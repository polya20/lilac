<script lang="ts">
  import {
    infiniteQuerySelectRows,
    queryDatasetSchema,
    querySelectRowsSchema,
    querySettings
  } from '$lib/queries/datasetQueries';
  import {
    getDatasetViewContext,
    getSelectRowsOptions,
    getSelectRowsSchemaOptions
  } from '$lib/stores/datasetViewStore';
  import {
    ITEM_SCROLL_CONTAINER_CTX_KEY,
    getHighlightedFields,
    getMediaFields
  } from '$lib/view_utils';
  import {L, ROWID, valueAtPath} from '$lilac';
  import {InlineNotification, SkeletonText} from 'carbon-components-svelte';
  import {setContext} from 'svelte';
  import InfiniteScroll from 'svelte-infinite-scroll';
  import {writable} from 'svelte/store';
  import FilterPanel from './FilterPanel.svelte';
  import RowItem from './RowItem.svelte';

  const datasetViewStore = getDatasetViewContext();

  $: schema = queryDatasetSchema($datasetViewStore.namespace, $datasetViewStore.datasetName);

  $: selectOptions = getSelectRowsOptions($datasetViewStore, true /* implicitSortByRowID */);

  $: settings = querySettings($datasetViewStore.namespace, $datasetViewStore.datasetName);

  $: selectRowsSchema = querySelectRowsSchema(
    $datasetViewStore.namespace,
    $datasetViewStore.datasetName,
    getSelectRowsSchemaOptions($datasetViewStore)
  );

  $: highlightedFields = getHighlightedFields($datasetViewStore.query, $selectRowsSchema?.data);

  $: rows = infiniteQuerySelectRows(
    $datasetViewStore.namespace,
    $datasetViewStore.datasetName,
    {...selectOptions, columns: [ROWID]} || {},
    $selectRowsSchema?.isSuccess ? $selectRowsSchema.data.schema : undefined
  );

  $: totalNumRows = $rows.data?.pages[0].total_num_rows;

  $: rowIds = $rows.data?.pages
    .flatMap(x => x.rows)
    .map(row => L.value(valueAtPath(row, [ROWID])!, 'string')!);

  $: mediaFields = $settings.data
    ? getMediaFields($selectRowsSchema?.data?.schema, $settings.data)
    : [];
  // Pass the item scroll container to children so they can handle scroll events.
  let itemScrollContainer: HTMLDivElement | null = null;
  const writableStore = writable<HTMLDivElement | null>(itemScrollContainer);
  $: setContext(ITEM_SCROLL_CONTAINER_CTX_KEY, writableStore);
  $: writableStore.set(itemScrollContainer);
</script>

<FilterPanel numRowsInQuery={totalNumRows} />

{#if $rows.isError}
  <InlineNotification
    lowContrast
    title="Could not fetch rows:"
    subtitle={$rows.error.body?.detail || $rows.error.message}
  />
{:else if $selectRowsSchema?.isError}
  <InlineNotification
    lowContrast
    title="Could not fetch schema:"
    subtitle={$selectRowsSchema.error.body?.detail || $selectRowsSchema?.error.message}
  />
{:else if $rows?.isSuccess && rowIds && rowIds.length === 0}
  <div class="mx-4 mt-8 w-full text-gray-600">No results.</div>
{/if}

{#if rowIds && $schema.isSuccess && mediaFields != null}
  <div
    class="flex h-full w-full flex-col gap-y-10 overflow-y-scroll pb-32"
    bind:this={itemScrollContainer}
  >
    {#each rowIds as rowId, i}
      <RowItem {rowId} index={i} {totalNumRows} {mediaFields} {highlightedFields} />
    {/each}
    {#if rowIds.length > 0}
      <InfiniteScroll threshold={100} on:loadMore={() => $rows?.fetchNextPage()} />
    {/if}
  </div>
{/if}

{#if $rows?.isFetching}
  <SkeletonText paragraph lines={3} />
{/if}
