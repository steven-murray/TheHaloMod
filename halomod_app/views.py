import datetime

# import logging
import io
import logging
import zipfile
from collections import OrderedDict

import numpy as np
from django.conf import settings
from django.core.mail import send_mail
from django.http import HttpResponse, HttpResponseRedirect
from django.views.generic.base import TemplateView
from django.views.generic.edit import FormView
import hmf
import halomod
from halomod import wdm, TracerHaloModel
from tabination.views import TabView

from . import forms
from . import utils
from . import version as calc_version

logger = logging.getLogger(__name__)


class BaseTab(TabView):
    """Base class for all main navigation tabs."""

    tab_group = "main"
    top = True


class home(BaseTab):
    """
    The home-page. Should just be simple html with links to what to do.
    """

    _is_tab = False
    template_name = "home.html"


class help(BaseTab):
    """
    A simple html 'end-page' which shows information about parameters used.
    """

    _is_tab = True
    tab_id = "/help/"
    tab_label = "Help"
    template_name = "help.html"


class CalculatorInputBase(FormView):
    """The form for input."""

    # Define the needed variables for FormView class
    form_class = forms.FrameworkInput
    success_url = "/halomod/"
    template_name = "calculator_form.html"

    def form_valid(self, form):
        """Define what to do if the form is valid."""
        label = form.cleaned_data["label"]

        if "objects" not in self.request.session:
            self.request.session["objects"] = OrderedDict()
        if "forms" not in self.request.session:
            self.request.session["forms"] = OrderedDict()

        self.request.session["objects"].update({label: form.halomod_obj})
        self.request.session["forms"].update({label: form.data})

        return super().form_valid(form)


class CalculatorInputCreate(CalculatorInputBase):
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        prev_label = self.kwargs.get("label", None)

        forms = self.request.session.get("forms", None)

        kwargs.update(
            current_models=self.request.session.get("objects", None),
            model_label=prev_label,
            initial=forms.get(prev_label, None) if prev_label else None,
        )
        return kwargs


class CalculatorInputEdit(CalculatorInputCreate):
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update(edit=True)
        return kwargs

    def get(self, request, *args, **kwargs):
        """
        Handles GET requests and instantiates a blank version of the form.
        """
        if kwargs.get("label", "") not in self.request.session.get("objects", {}):
            return HttpResponseRedirect("/halomod/create/")

        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        result = super().form_valid(form)

        # If editing, and the label was changed, we need to remove the old label.
        if form.cleaned_data["label"] != self.kwargs["label"]:
            del self.request.session["objects"][self.kwargs["label"]]

            try:
                del self.request.session["forms"][self.kwargs["label"]]
            except KeyError:
                # Special-case the original 'default', since it doesn't exist as a form
                # at first.
                if self.kwargs["label"] != "default":
                    raise

        return result


def delete_plot(request, label):
    if len(request.session["objects"]) > 1:

        try:
            del request.session["objects"][label]
        except KeyError:
            pass

        try:
            del request.session["forms"][label]
        except KeyError:
            pass

    return HttpResponseRedirect("/halomod/")


def complete_reset(request):
    try:
        del request.session["objects"]
        del request.session["forms"]
    except KeyError:
        pass

    return HttpResponseRedirect("/halomod/")


class ViewPlots(BaseTab):
    def get(self, request, *args, **kwargs):
        # Create a default TracerHaloModel object that displays upon opening.
        if "objects" not in request.session:
            default_obj = TracerHaloModel(hod_params={"central": True})

            request.session["objects"] = OrderedDict(default=default_obj)
            request.session["forms"] = OrderedDict()

        self.form = forms.PlotChoice(request)

        print("IN ViewPlots:", list(request.session["forms"].keys()))
        self.warnings = ""  # request.session['warnings']
        return self.render_to_response(
            self.get_context_data(
                form=self.form,
                warnings=self.warnings,
                objects=request.session["objects"],
            )
        )

    template_name = "image_page.html"
    _is_tab = True
    tab_id = "/halomod/"
    tab_label = "Calculator"
    top = True


def plots(request, filetype, plottype):
    """
    Chooses the type of plot needed and the filetype (pdf or png) and outputs it
    """
    objects = request.session.get("objects", None)

    keymap = {
        **utils.KEYMAP,
        "comparison_dndm": {
            "xlab": utils.MLABEL,
            "ylab": r"Ratio of Mass Functions $ \left(\frac{dn}{dM}\right) / \left( \frac{dn}{dM} \right)_{%s} $"
            % list(objects.keys())[0],
            "yscale": "log",
            "basey": 2,
        },
        "comparison_fsigma": {
            "xlab": utils.MLABEL,
            "ylab": r"Ratio of Fitting Functions $f(\sigma)/ f(\sigma)_{%s}$"
            % list(objects.keys())[0],
            "yscale": "log",
            "basey": 2,
        },
    }
    if not objects:
        return HttpResponseRedirect("/halomod/")

    if filetype not in ["png", "svg", "pdf", "zip"]:
        raise ValueError(f"{filetype} is not a valid plot filetype")

    figure_buf = utils.create_canvas(
        objects, plottype, keymap[plottype], plot_format=filetype
    )

    # How to output the image
    if filetype == "png":
        response = HttpResponse(figure_buf.getvalue(), content_type="image/png")
    elif filetype == "svg":
        response = HttpResponse(figure_buf.getvalue(), content_type="image/svg+xml")
    elif filetype == "pdf":
        response = HttpResponse(figure_buf.getvalue(), content_type="application/pdf")
        response["Content-Disposition"] = "attachment;filename=" + plottype + ".pdf"
    elif filetype == "zip":
        response = io.StringIO()

    return response


def header_txt(request):
    # Set up the response object as a text file
    response = HttpResponse(content_type="text/plain")
    response["Content-Disposition"] = "attachment; filename=parameters.txt"

    # Import all the input form data so it can be written to file
    objects = request.session["objects"]

    labels = list(objects.keys())
    objects = list(objects.values())

    # Write the parameter info
    response.write("File Created On: " + str(datetime.datetime.now()) + "\n")
    response.write("With version " + calc_version + " of halomod_app \n")
    response.write("And version " + hmf.__version__ + " of hmf (backend) \n")
    response.write("And version " + halomod.__version__ + " of halomod (backend) \n")

    response.write("\n")
    response.write("SETS OF PARAMETERS USED \n")

    for i, o in enumerate(objects):
        response.write("=====================================================\n")
        response.write("   %s\n" % (labels[i]))
        response.write("=====================================================\n")
        for k, v in list(o.parameter_values.items()):
            response.write("%s: %s \n" % (k, v))
        response.write("\n")

        return response


def data_output(request):
    # TODO: output HDF5 format
    # Import all the data we need
    objects = request.session["objects"]

    labels = list(objects.keys())
    objects = list(objects.values())

    # Open up file-like objects for response
    response = HttpResponse(content_type="application/zip")
    response["Content-Disposition"] = "attachment; filename=all_plots.zip"
    buff = io.BytesIO()
    archive = zipfile.ZipFile(buff, "w", zipfile.ZIP_DEFLATED)

    # Write out mass-based, k-based and r-based data files
    for i, o in enumerate(objects):
        for kind in utils.XLABELS:

            s = io.BytesIO()

            s.write(f"# [0] {utils.XLABELS[kind]}".encode())

            items = {
                k: utils.KEYMAP[k]["ylab"]
                for k in utils.KEYMAP
                if utils.KEYMAP[k]["xlab"] == utils.XLABELS[kind]
            }

            for j, (label, ylab) in enumerate(items.items()):
                if getattr(o, label) is not None:
                    s.write(f"# [{j+1}] {ylab}".encode())

            out = np.array(
                [getattr(o, kind)]
                + [
                    getattr(o, label)
                    for label in items
                    if getattr(o, label) is not None
                ]
            ).T
            np.savetxt(s, out)

            archive.writestr(f"{kind}Vector_{labels[i]}.txt", s.getvalue())

            s.close()

    archive.close()
    buff.flush()
    ret_zip = buff.getvalue()
    buff.close()
    response.write(ret_zip)
    return response


def halogen(request):
    # Import all the data we need
    objects = request.session["objects"]

    labels = list(objects.keys())
    objects = list(objects.values())

    # Open up file-like objects for response
    response = HttpResponse(content_type="application/zip")
    response["Content-Disposition"] = "attachment; filename=halogen.zip"
    buff = io.BytesIO()
    archive = zipfile.ZipFile(buff, "w", zipfile.ZIP_DEFLATED)

    # Write out ngtm and lnP data files
    for i, o in enumerate(objects):
        s = io.BytesIO()

        # MASS BASED
        out = np.array([o.m, o.ngtm]).T
        np.savetxt(s, out)

        archive.writestr("ngtm_%s.txt" % labels[i], s.getvalue())

        s.close()
        s = io.StringIO()

        # K BASED
        out = np.array([o.k, o.power]).T
        np.savetxt(s, out)
        archive.writestr("matterpower_%s.txt" % labels[i], s.getvalue())

    archive.close()
    buff.flush()
    ret_zip = buff.getvalue()
    buff.close()
    response.write(ret_zip)
    return response


class ContactFormView(FormView):
    form_class = forms.ContactForm
    template_name = "email_form.html"
    success_url = "/email-sent/"

    def form_valid(self, form):
        message = "{name} / {email} said: ".format(
            name=form.cleaned_data.get("name"), email=form.cleaned_data.get("email")
        )
        message += "\n\n{0}".format(form.cleaned_data.get("message"))
        send_mail(
            subject=form.cleaned_data.get("subject").strip(),
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[settings.CONTACT_RECIPIENTS],
        )
        return super(ContactFormView, self).form_valid(form)


class EmailSuccess(TemplateView):
    template_name = "email_sent.html"


# ===============================================================================
# Some views that just return downloadable content
# ===============================================================================
def get_code(request, name):
    suffix = name.split(".")[-1]

    with open(name, "r") as f:
        if suffix == "pdf":
            response = HttpResponse(f.read(), content_type="application/pdf")
        elif suffix == "py":
            response = HttpResponse(f.read(), content_type="text/plain")
        elif suffix == "zip":
            response = HttpResponse(f.read(), content_type="application/zip")

        response["Content-Disposition"] = "attachment;filename=" + name
        return response
