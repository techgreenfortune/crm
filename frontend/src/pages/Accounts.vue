<template>
  <LayoutHeader>
    <template #left-header>
      <ViewBreadcrumbs v-model="viewControls" routeName="Accounts" />
    </template>
    <template #right-header>
      <CustomActions
        v-if="accountsListView?.customListActions"
        :actions="accountsListView.customListActions"
      />
      <Button
        variant="solid"
        :label="__('Create')"
        iconLeft="plus"
        @click="showAccountModal = true"
      />
    </template>
  </LayoutHeader>
  <ViewControls
    ref="viewControls"
    v-model="accounts"
    v-model:loadMore="loadMore"
    v-model:resizeColumn="triggerResize"
    v-model:updatedPageCount="updatedPageCount"
    doctype="CRM Account"
  />
  <AccountsListView
    v-if="accounts.data && rows.length"
    ref="accountsListView"
    v-model="accounts.data.page_length_count"
    v-model:list="accounts"
    :rows="rows"
    :columns="columns"
    :options="{
      showTooltip: false,
      resizeColumn: true,
      rowCount: accounts.data.row_count,
      totalCount: accounts.data.total_count,
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
    v-else-if="accounts.data && !rows.length"
    name="Accounts"
    :icon="AccountsIcon"
  />
  <AccountModal v-if="showAccountModal" v-model="showAccountModal" />
</template>
<script setup>
import ViewBreadcrumbs from '@/components/ViewBreadcrumbs.vue'
import CustomActions from '@/components/CustomActions.vue'
import AccountsIcon from '@/components/Icons/AccountsIcon.vue'
import LayoutHeader from '@/components/LayoutHeader.vue'
import AccountModal from '@/components/Modals/AccountModal.vue'
import AccountsListView from '@/components/ListViews/AccountsListView.vue'
import ViewControls from '@/components/ViewControls.vue'
import { getMeta } from '@/stores/meta'
import { formatDate, timeAgo } from '@/utils'
import { ref, computed } from 'vue'
import EmptyState from '@/components/ListViews/EmptyState.vue'

const { getFormattedPercent, getFormattedFloat, getFormattedCurrency } =
  getMeta('CRM Account')

const accountsListView = ref(null)
const showAccountModal = ref(false)

// accounts data is loaded in the ViewControls component
const accounts = ref({})
const loadMore = ref(1)
const triggerResize = ref(1)
const updatedPageCount = ref(20)
const viewControls = ref(null)

const rows = computed(() => {
  if (
    !accounts.value?.data?.data ||
    !['list', 'group_by'].includes(accounts.value.data.view_type)
  )
    return []
  return accounts.value?.data.data.map((account) => {
    let _rows = {}
    accounts.value?.data.rows.forEach((row) => {
      _rows[row] = account[row]

      let fieldType = accounts.value?.data.columns?.find(
        (col) => (col.key || col.value) == row,
      )?.type

      if (
        fieldType &&
        ['Date', 'Datetime'].includes(fieldType) &&
        !['modified', 'creation'].includes(row)
      ) {
        _rows[row] = formatDate(account[row], '', true, fieldType == 'Datetime')
      }

      if (fieldType && fieldType == 'Currency') {
        _rows[row] = getFormattedCurrency(row, account)
      }

      if (fieldType && fieldType == 'Float') {
        _rows[row] = getFormattedFloat(row, account)
      }

      if (fieldType && fieldType == 'Percent') {
        _rows[row] = getFormattedPercent(row, account)
      }

      if (row === 'account_name') {
        _rows[row] = {
          label: account.account_name,
          logo: account.account_logo,
        }
      } else if (['modified', 'creation'].includes(row)) {
        _rows[row] = {
          label: formatDate(account[row]),
          timeAgo: __(timeAgo(account[row])),
        }
      }
    })
    return _rows
  })
})

const columns = computed(() => {
  let _columns = accounts.value?.data?.columns || []

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
