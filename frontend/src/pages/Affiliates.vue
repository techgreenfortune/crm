<template>
  <LayoutHeader>
    <template #left-header>
      <ViewBreadcrumbs v-model="viewControls" routeName="Affiliates" />
    </template>
    <template #right-header>
      <CustomActions
        v-if="affiliatesListView?.customListActions"
        :actions="affiliatesListView.customListActions"
      />
      <Button
        variant="solid"
        :label="__('Create')"
        iconLeft="plus"
        @click="showAffiliateModal = true"
      />
    </template>
  </LayoutHeader>
  <ViewControls
    ref="viewControls"
    v-model="affiliates"
    v-model:loadMore="loadMore"
    v-model:resizeColumn="triggerResize"
    v-model:updatedPageCount="updatedPageCount"
    doctype="CRM Affiliate"
  />
  <AffiliatesListView
    v-if="affiliates.data && rows.length"
    ref="affiliatesListView"
    v-model="affiliates.data.page_length_count"
    v-model:list="affiliates"
    :rows="rows"
    :columns="columns"
    :options="{
      showTooltip: false,
      resizeColumn: true,
      rowCount: affiliates.data.row_count,
      totalCount: affiliates.data.total_count,
    }"
    @loadMore="() => loadMore++"
    @columnWidthUpdated="() => triggerResize++"
    @updatePageCount="(count) => (updatedPageCount = count)"
    @applyFilter="(data) => viewControls.applyFilter(data)"
    @applyLikeFilter="(data) => viewControls.applyLikeFilter(data)"
    @likeDoc="(data) => viewControls.likeDoc(data)"
    @selectionsChanged="
      (selections) => viewControls.updateSelections(selections)
    "
  />
  <EmptyState
    v-else-if="affiliates.data && !rows.length"
    name="Affiliates"
    :icon="AffiliatesIcon"
  />
  <AffiliateModal v-if="showAffiliateModal" v-model="showAffiliateModal" />
</template>
<script setup>
import ViewBreadcrumbs from '@/components/ViewBreadcrumbs.vue'
import CustomActions from '@/components/CustomActions.vue'
import AffiliatesIcon from '@/components/Icons/AffiliatesIcon.vue'
import LayoutHeader from '@/components/LayoutHeader.vue'
import AffiliateModal from '@/components/Modals/AffiliateModal.vue'
import AffiliatesListView from '@/components/ListViews/AffiliatesListView.vue'
import ViewControls from '@/components/ViewControls.vue'
import { getMeta } from '@/stores/meta'
import { formatDate, timeAgo } from '@/utils'
import { ref, computed } from 'vue'
import EmptyState from '@/components/ListViews/EmptyState.vue'

const { getFormattedPercent, getFormattedFloat, getFormattedCurrency } =
  getMeta('CRM Affiliate')

const affiliatesListView = ref(null)
const showAffiliateModal = ref(false)

// affiliates data is loaded in the ViewControls component
const affiliates = ref({})
const loadMore = ref(1)
const triggerResize = ref(1)
const updatedPageCount = ref(20)
const viewControls = ref(null)

const rows = computed(() => {
  if (
    !affiliates.value?.data?.data ||
    !['list', 'group_by'].includes(affiliates.value.data.view_type)
  )
    return []
  return affiliates.value?.data.data.map((affiliate) => {
    let _rows = {}
    affiliates.value?.data.rows.forEach((row) => {
      _rows[row] = affiliate[row]

      let fieldType = affiliates.value?.data.columns?.find(
        (col) => (col.key || col.value) == row,
      )?.type

      if (
        fieldType &&
        ['Date', 'Datetime'].includes(fieldType) &&
        !['modified', 'creation'].includes(row)
      ) {
        _rows[row] = formatDate(
          affiliate[row],
          '',
          true,
          fieldType == 'Datetime',
        )
      }

      if (fieldType && fieldType == 'Currency') {
        _rows[row] = getFormattedCurrency(row, affiliate)
      }

      if (fieldType && fieldType == 'Float') {
        _rows[row] = getFormattedFloat(row, affiliate)
      }

      if (fieldType && fieldType == 'Percent') {
        _rows[row] = getFormattedPercent(row, affiliate)
      }

      if (row === 'affiliate_name') {
        _rows[row] = {
          label: affiliate.affiliate_name,
        }
      } else if (['modified', 'creation'].includes(row)) {
        _rows[row] = {
          label: formatDate(affiliate[row]),
          timeAgo: __(timeAgo(affiliate[row])),
        }
      }
    })
    return _rows
  })
})

const columns = computed(() => {
  let _columns = affiliates.value?.data?.columns || []

  if (_columns.length) {
    _columns = _columns.map((col, index) => {
      if (index === _columns.length - 1) {
        return { ...col, align: 'right' }
      }
      return col
    })
  }

  return _columns
})
</script>
