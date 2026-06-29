<template>
  <Dialog v-model="show" :options="{ size: 'xl' }">
    <template #body>
      <div class="px-4 pt-5 pb-6 bg-surface-modal sm:px-6">
        <div class="flex items-center justify-between mb-5">
          <div>
            <h3 class="text-2xl font-semibold leading-6 text-ink-gray-9">
              {{ __('New Affiliate') }}
            </h3>
          </div>
          <div class="flex items-center gap-1">
            <Button
              v-if="isManager() && !isMobileView"
              variant="ghost"
              class="w-7"
              :tooltip="__('Edit Fields Layout')"
              :icon="EditIcon"
              @click="openQuickEntryModal"
            />
            <Button
              variant="ghost"
              class="w-7"
              icon="x"
              @click="show = false"
            />
          </div>
        </div>
        <FieldLayout
          v-if="tabs.data?.length"
          :tabs="tabs.data"
          :data="affiliate.doc"
          doctype="CRM Affiliate"
        />
        <ErrorMessage v-if="error" class="mt-8" :message="__(error)" />
      </div>
      <div class="px-4 pt-4 pb-7 sm:px-6">
        <div class="space-y-2">
          <Button
            class="w-full"
            variant="solid"
            :label="__('Create')"
            :loading="loading"
            @click="createAffiliate"
          />
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import FieldLayout from '@/components/FieldLayout/FieldLayout.vue'
import EditIcon from '@/components/Icons/EditIcon.vue'
import { usersStore } from '@/stores/users'
import { isMobileView } from '@/composables/settings'
import { showQuickEntryModal, quickEntryProps } from '@/composables/modals'
import { useDocument } from '@/data/document'
import { useTelemetry } from 'frappe-ui/frappe'
import { call, createResource } from 'frappe-ui'
import { ref, nextTick, onMounted } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  data: { type: Object, default: () => ({}) },
  options: {
    type: Object,
    default: () => ({ redirect: true, afterInsert: () => {} }),
  },
})

const { isManager } = usersStore()
const { capture } = useTelemetry()

const router = useRouter()
const show = defineModel({ type: Boolean })

const loading = ref(false)
const error = ref(null)

const { document: affiliate } = useDocument('CRM Affiliate')

async function createAffiliate() {
  loading.value = true
  error.value = null

  const doc = await call(
    'frappe.client.insert',
    {
      doc: {
        doctype: 'CRM Affiliate',
        ...affiliate.doc,
      },
    },
    {
      onError: (err) => {
        error.value = err.error?.messages?.[0]
        loading.value = false
      },
    },
  )
  loading.value = false
  if (doc.name) {
    capture('affiliate_created')
    handleAffiliateUpdate(doc)
    affiliate.doc = {}
  }
}

function handleAffiliateUpdate(doc) {
  if (doc.name && props.options.redirect) {
    router.push({
      name: 'Affiliate',
      params: { affiliateId: doc.name },
    })
  }
  show.value = false
  props.options.afterInsert?.(doc)
}

const tabs = createResource({
  url: 'crm.fcrm.doctype.crm_fields_layout.crm_fields_layout.get_fields_layout',
  cache: ['QuickEntry', 'CRM Affiliate'],
  params: { doctype: 'CRM Affiliate', type: 'Quick Entry' },
  auto: true,
  transform: (_tabs) => {
    _tabs.forEach((tab) => {
      tab.sections.forEach((section) => {
        section.columns.forEach((column) => {
          column.fields.forEach((field) => {
            if (field.fieldtype === 'Table') {
              affiliate.doc[field.fieldname] = []
            }
          })
        })
      })
    })
    return _tabs
  },
})

onMounted(() => {
  Object.assign(affiliate.doc, props.data)
})

function openQuickEntryModal() {
  showQuickEntryModal.value = true
  quickEntryProps.value = { doctype: 'CRM Affiliate' }
  nextTick(() => (show.value = false))
}
</script>
