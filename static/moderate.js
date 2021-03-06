'use strict'

Vue.component('moderate', {
  template: `
    <div>
      <h2>
        Upload by <a :href='"https://github.com/" + asset.user'>{{asset.user}}</a>
      </h2>
      <div class='embed-responsive embed-responsive-16by9' v-if='asset.filetype == "video"'>
        <video class="embed-responsive-item" width="1920" height="1080" controls autoplay loop muted>
          <source :src='asset.url' type='video/mp4'/>
        </video>
      </div>
      <img class='img-responsive' :src='asset.url' v-else/>
      <hr/>
      <template v-if='needs_moderation'>
        <p class='text-centered'>
          Current state: <b>{{asset.state}}</b>
        </p>
        <div class='row'>
          <div class='col-xs-6'>
            <button @click='moderate("confirm")' class='btn btn-lg btn-block btn-success'>
              Confirm
            </button>
          </div>
          <div class='col-xs-6'>
            <button @click='moderate("reject")' class='btn btn-lg btn-block btn-danger'>
              Reject
            </button>
          </div>
        </div>
      </template>
      <div class='alert alert-success text-centered' v-if='completed'>
        Thanks for moderating.
      </div>
    </div>
  `,
  data: () => ({
    needs_moderation: true,
    completed: false,
  }),
  props: ['sig', 'asset'],
  methods: {
    async moderate(result) {
      this.needs_moderation = false
      await Vue.nextTick()
      await Vue.http.post(`/content/moderate/${this.asset.id}-${this.sig}/${result}`)
      this.completed = true
    },
  },
})

new Vue({el: "#main"})
