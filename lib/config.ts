import { runtimeConfig } from "./runtime-config";

/**
 * What the product is called, read when the page runs.
 *
 * Getters, not constants: the values arrive with the request, so reading them
 * at module load would freeze whatever the build happened to see.
 */
export const product = {
  get name() {
    return runtimeConfig().product.name;
  },
  get tagline() {
    return runtimeConfig().product.tagline;
  },
  get description() {
    return runtimeConfig().product.description;
  },
  get disclaimer() {
    return runtimeConfig().product.disclaimer;
  },
  get aiDisclosure() {
    return runtimeConfig().product.aiDisclosure;
  },
  get locale() {
    return runtimeConfig().product.locale;
  },
  get monogram() {
    return runtimeConfig().product.monogram;
  },
  get badges() {
    return runtimeConfig().product.badges;
  },
};

export const emptyState = {
  get eyebrow() {
    return runtimeConfig().emptyState.eyebrow;
  },
  get headline() {
    return runtimeConfig().emptyState.headline;
  },
  get subhead() {
    return runtimeConfig().emptyState.subhead;
  },
  get body() {
    return runtimeConfig().emptyState.body;
  },
};
