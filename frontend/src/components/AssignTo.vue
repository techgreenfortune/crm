<template>
  <Popover placement="bottom-end">
    <template #target="{ togglePopover }">
      <div class="flex items-center" @click="togglePopover">
        <component
          :is="assignees?.length == 1 ? 'Button' : 'div'"
          v-if="assignees?.length"
        >
          <MultipleAvatar :avatars="assignees" />
        </component>
        <Button v-else :label="__('Assign To')" />
      </div>
    </template>
    <template #body="{ isOpen }">
      <AssignToBody
        v-show="isOpen"
        v-model="assignees"
        :docname="docname"
        :doctype="doctype"
        :open="isOpen"
        :onUpdate="ownerField && saveAssignees"
      />
    </template>
  </Popover>
</template>
<script setup>
import MultipleAvatar from '@/components/MultipleAvatar.vue'
import AssignToBody from '@/components/AssignToBody.vue'
import { useDocument } from '@/data/document'
import { toast, Popover } from 'frappe-ui'
import { computed } from 'vue'

const props = defineProps({
  doctype: { type: String, default: '' },
  docname: { type: String, default: '' },
})

const { document } = useDocument(props.doctype, props.docname)

const assignees = defineModel({ type: Array, default: () => [] })

const ownerField = computed(() => {
  if (props.doctype === 'CRM Lead') {
    return 'lead_owner'
  } else if (props.doctype === 'CRM Deal') {
    return 'deal_owner'
  } else {
    return null
  }
})

async function saveAssignees(
  addedAssignees,
  removedAssignees,
  addAssignees,
  removeAssignees,
) {
  if (removedAssignees.length) await removeAssignees.submit(removedAssignees)
  if (addedAssignees.length) await addAssignees.submit(addedAssignees)

  const nextAssignee = assignees.value.find(
    (a) => a.name !== document.doc[ownerField.value],
  )

  let owner = ownerField.value.replace('_', ' ')

  // Use setValue (frappe.client.set_value) instead of save (frappe.client.save)
  // so that only the owner field is submitted. document.save triggers full
  // document validation including mandatory fields unrelated to this change
  // (e.g. custom_pincode), causing the owner update to silently fail.
  async function setOwner(newValue) {
    document.doc[ownerField.value] = newValue
    await document.setValue.submit({ [ownerField.value]: newValue })
  }

  if (
    document.doc[ownerField.value] &&
    removedAssignees.includes(document.doc[ownerField.value])
  ) {
    await setOwner(nextAssignee ? nextAssignee.name : '')

    if (nextAssignee) {
      toast.info(
        __(
          'Since you removed {0} from the assignee, the {0} has been changed to the next available assignee {1}.',
          [owner, nextAssignee.label || nextAssignee.name],
        ),
      )
    } else {
      toast.info(
        __(
          'Since you removed {0} from the assignee, the {0} has also been removed.',
          [owner],
        ),
      )
    }
  } else if (!document.doc[ownerField.value] && nextAssignee) {
    await setOwner(nextAssignee.name)
    toast.info(
      __('Since you added a new assignee, the {0} has been set to {1}.', [
        owner,
        nextAssignee.label || nextAssignee.name,
      ]),
    )
  } else if (addedAssignees.length && nextAssignee) {
    await setOwner(nextAssignee.name)
  }
}
</script>
