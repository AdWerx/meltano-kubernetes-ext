A meltano orchestrator utility for generating Kubernetes CronJob manifests from meltano schedules. The output format is a [Kustomize](https://kustomize.io/) base layer that can be overlayed with your required modifications to the manifests.

## Installation

Add the utility to your meltano configuration:

```yaml
plugins:
  utilities:
    - name: kubernetes
      namespace: kubernetes_ext
      type: orchestrators
      pip_url: git+https://github.com/AdWerx/meltano-kubernetes-ext.git@GIT_TAG_OR_REF_HERE
      executable: kubernetes
```

where `GIT_TAG_OR_REF_HERE` is a git tag or branch reference that exists in this repository.

Then run meltano install:

```
meltano install
```

## Rendering / generating the manifests

This utility will read the output of `meltano schedule list --format=json` and template a kubernetes CronJob for each entry in the schedule with a valid cron interval. `@once` and `@manual` are not supported intervals at this time. 

### The `render` command

Example: `meltano invoke kubernetes render`

The `render` subcommand will read the meltano schedule and create a CronJob manifest for each scheduled job or EL pair. The CronJob manifests and a `kustomization.yaml` will be written to `orchestrate/kubernetes/base` (see `meltano invoke kubernetes --help` for information on overriding the destination directory). These files are managed by the extension and should not be modified as they will be re-created when invoking `render` again. To make customizations to the manifests, use a [Kustomize Overlay](https://kubernetes.io/docs/tasks/manage-kubernetes-objects/kustomization/#bases-and-overlays).

The command will also template some files in `orchestrate/kubernetes/overlays/$MELATNO_ENVIRONMENT`, providing an example set of [overlay files](https://kubernetes.io/docs/tasks/manage-kubernetes-objects/kustomization/#bases-and-overlays) in order for you to customize the kubernetes manifests any way you see fit. An overlay will allow you to set the _image_ you would like to use, add annotations, add sidecars, override resource requests, or other patches.

## Overlays

[Kustomize overlays](https://kubernetes.io/docs/tasks/manage-kubernetes-objects/kustomization/#bases-and-overlays) can be used to customize the CronJob manifests that are templating in the base layer. An overlay can be used to:

- Specify a namespace for the kubernetes manifests
- Specify an image and tag to be used for all CronJobs
- Specify resource requests/limits to be used for any number of CronJobs
- Specify extra secrets, volumes, or anything else Kustomize can do to be applied to one or many CronJobs

In the examples below, the overlay is named `production`, which would be templated automatically if your invoked this utility with `meltano --environment production invoke kubernetes render`. The value of `MELTANO_ENVIRONMENT` will be used as the overlay name.

### Specifying a destination namespace

To specify a namespace for your CronJob resources, add the `namespace:` key to the kustomization.yml file in your overlay:

```yaml
# orchestrate/kubernetes/overlays/production/kustomization.yml

resources:
  - ../../base
namespace: meltano
```

After specifying this key value pair, all manifests rendered with kustomize will have this namespace in their metadata.

### Specifying an image and tag

To specify an image and tag to use in your CronJobs, set the `images:` value in the overlay kustomization.yml:

```yaml
# orchestrate/kubernetes/overlays/production/kustomization.yml

resources:
  - ../../base
images:
  - name: meltano
    newName: somedockerregistry.cloud.dev/org/image
    newTag: xyz123
```

Using `name: meltano` is required in order to replace the default image name templated in the CronJob manifests.

### Adding Secret or ConfigMap references to environment variables

In order to provide CronJob pods with credentials and other plugin settings, you may want to map secrets into the pods via environment variables. To do so, create a patch to apply to the CronJob manifests:

```yaml
# orchestrate/kubernetes/overlays/production/secrets.yml

apiVersion: batch/v1
kind: CronJob
metadata:
  name: any
spec:
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: meltano
              env:
                - name: TAP_MYSQL__WAREHOUSE_USER
                  valueFrom:
                    secretKeyRef:
                      key: username
                      name: db-credentials
                      optional: false
                - name: TAP_MYSQL__DB_WAREHOUSE_PASSWORD
                  valueFrom:
                    secretKeyRef:
                      key: password
                      name: db-credentials
                      optional: false
              envFrom:
                # always include this configmap if overriding `envFrom`
                - configMapRef:
                    name: env
                    optional: false
                - secretRef:
                    name: salesforce-credentials
                    optional: false
```

You can apply this patch to any number of CronJob resources by adding the patch and a target to the kustomization.yml of your target overlay:

```yaml
# orchestrate/kubernetes/overlays/production/kustomization.yml

patches:
  - path: secrets.yml
    target:
      kind: CronJob
      # you could optionally apply this patch to only specific meltano
      # schedules by targeting them by their label like the below example:
      # labelSelector: "meltano.kubernetes.io/schedule=db-warehouse-daily"
```

`labelSelector` can be used to [target any number of CronJobs rendered by Kustomise](https://github.com/kubernetes-sigs/kustomize/blob/master/examples/patchMultipleObjects.md).

**Note** always include the `env` ConfigMap in `envFrom` if you override `envFrom` as `envFrom` is not merged, but overwritten when making a patch due to the fact it does not use a strategic merge.

### Adding resources requests to all CronJobs

To add resource requests to all CronJobs templated by this utility, add a patch file to the overlay directory matching the meltano environment you're targeting:

```yaml
# orchestrate/kubernetes/overlays/production/resources.yml

apiVersion: batch/v1
kind: CronJob
metadata:
  name: any
spec:
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: meltano
              resources:
                requests:
                  cpu: "1"
                  ephemeral-storage: 1Gi
                  memory: 4Gi
                limits:
                  cpu: "1"
                  ephemeral-storage: 1Gi
                  memory: 4Gi
```

and provide this patch instruction to Kustomize with the following:

```yaml
# orchestrate/kubernetes/overlays/production/kustomization.yml

resources:
  - ../../base
patches:
  - path: resources.yml
    target:
      kind: CronJob
```

The above patch and patch targeting will *apply to all CronJob resources*, adding resource requests and limits to each Job's PodSpec.

### Adding non-secret values via the ConfigMap

The CronJob manifests will be accompanied by a ConfigMap manifest that contains the `MELTANO_ENVIRONMENT` variable set to the value during `render` invocation and matching the overlay name. To add more values to this ConfigMap, which will be mounted as environment variables to the meltano process, add or edit the following file:

```yaml
# orchestrate/kubernetes/overlays/production/env-config-map.yml

apiVersion: v1
kind: ConfigMap
metadata:
  name: env
data:
  MELTANO_ENVIRONMENT: "production"
  # new value added below
  MELTANO_CLI_LOG_CONFIG: /project/logs-json.yml
```

and include this patch in the kustomization.yml

```yaml
resources:
  - ../../base
patches:
  - env-config-map.yml
``

## Contributing

1. Install the project dependencies with `poetry install`:

```shell
cd path/to/this/repo
poetry install
```

2. Verify that you can invoke the extension:

```shell
MELTANO_PROJECT_ROOT=... poetry run kubernetes --help
MELTANO_PROJECT_ROOT=... poetry run kubernetes describe --format=yaml
```

## Template updates

This project was generated with [copier](https://copier.readthedocs.io/en/stable/) from the [Meltano EDK template](https://github.com/meltano/edk).
